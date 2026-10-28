#include "codex_status_receiver.h"

#include <cerrno>
#include <cstdio>
#include <cstring>
#include <string>
#include <utility>

#include <esp_log.h>
#include <lwip/sockets.h>

namespace {
constexpr char kProtocolPrefix[] = "XINGZHI_CODEX_V1";
constexpr char kAckPrefix[] = "XINGZHI_CODEX_ACK_V1";
constexpr char kVoicePrefix[] = "XINGZHI_VOICE_V1 ";
constexpr size_t kMaxPacketSize = 96;
constexpr size_t kMaxTranscriptSize = 1024;
constexpr int kTaskStackSize = 4096;
constexpr UBaseType_t kTaskPriority = 2;
const char* const kTag = "CodexStatusReceiver";
}  // namespace

CodexStatusReceiver::CodexStatusReceiver(StateChangedCallback state_changed_callback,
                                         SummaryCallback summary_callback,
                                         FailureCallback failure_callback)
    : state_changed_callback_(std::move(state_changed_callback)),
      summary_callback_(std::move(summary_callback)),
      failure_callback_(std::move(failure_callback)) {}

CodexStatusReceiver::~CodexStatusReceiver() {
    stop_requested_.store(true);
    if (task_handle_ != nullptr) {
        xTaskNotifyGive(task_handle_);
    }
}

void CodexStatusReceiver::Start() {
    bool expected = false;
    if (!started_.compare_exchange_strong(expected, true)) {
        return;
    }

    BaseType_t result = xTaskCreate(TaskEntry, "codex_status_rx", kTaskStackSize, this,
                                    kTaskPriority, &task_handle_);
    if (result != pdPASS) {
        task_handle_ = nullptr;
        started_.store(false);
        ESP_LOGE(kTag, "Failed to create UDP receiver task");
    }
}

bool CodexStatusReceiver::SendTranscript(std::string_view text) const {
    uint32_t host_address = host_address_.load();
    if (host_address == 0) {
        ESP_LOGW(kTag, "Cannot forward STT: Codex host is unknown");
        return false;
    }
    if (text.empty() || text.size() > kMaxTranscriptSize ||
        std::memchr(text.data(), '\0', text.size()) != nullptr) {
        ESP_LOGW(kTag, "Cannot forward invalid STT text (%u bytes)",
                 static_cast<unsigned>(text.size()));
        return false;
    }

    int socket_fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (socket_fd < 0) {
        ESP_LOGE(kTag, "voice socket() failed: errno=%d", errno);
        return false;
    }
    sockaddr_in destination = {};
    destination.sin_family = AF_INET;
    destination.sin_port = htons(kVoicePort);
    destination.sin_addr.s_addr = host_address;

    std::string packet(kVoicePrefix);
    packet.append(text.data(), text.size());
    int sent = sendto(socket_fd, packet.data(), packet.size(), 0,
                      reinterpret_cast<sockaddr*>(&destination), sizeof(destination));
    close(socket_fd);
    if (sent != static_cast<int>(packet.size())) {
        ESP_LOGE(kTag, "Failed to forward STT: errno=%d", errno);
        return false;
    }
    ESP_LOGI(kTag, "Forwarded STT to Codex host (%u bytes)",
             static_cast<unsigned>(text.size()));
    return true;
}

void CodexStatusReceiver::TaskEntry(void* arg) {
    static_cast<CodexStatusReceiver*>(arg)->Run();
}

void CodexStatusReceiver::Run() {
    int socket_fd = socket(AF_INET, SOCK_DGRAM, IPPROTO_IP);
    if (socket_fd < 0) {
        ESP_LOGE(kTag, "socket() failed: errno=%d", errno);
        started_.store(false);
        task_handle_ = nullptr;
        vTaskDelete(nullptr);
        return;
    }

    int reuse_address = 1;
    if (setsockopt(socket_fd, SOL_SOCKET, SO_REUSEADDR, &reuse_address,
                   sizeof(reuse_address)) != 0) {
        ESP_LOGE(kTag, "setsockopt(SO_REUSEADDR) failed: errno=%d", errno);
        close(socket_fd);
        started_.store(false);
        task_handle_ = nullptr;
        vTaskDelete(nullptr);
        return;
    }

    timeval receive_timeout = {.tv_sec = 1, .tv_usec = 0};
    if (setsockopt(socket_fd, SOL_SOCKET, SO_RCVTIMEO, &receive_timeout,
                   sizeof(receive_timeout)) != 0) {
        ESP_LOGE(kTag, "setsockopt(SO_RCVTIMEO) failed: errno=%d", errno);
        close(socket_fd);
        started_.store(false);
        task_handle_ = nullptr;
        vTaskDelete(nullptr);
        return;
    }

    sockaddr_in listen_address = {};
    listen_address.sin_family = AF_INET;
    listen_address.sin_port = htons(kPort);
    listen_address.sin_addr.s_addr = htonl(INADDR_ANY);
    if (bind(socket_fd, reinterpret_cast<sockaddr*>(&listen_address), sizeof(listen_address)) !=
        0) {
        ESP_LOGE(kTag, "bind(%u) failed: errno=%d", kPort, errno);
        close(socket_fd);
        started_.store(false);
        task_handle_ = nullptr;
        vTaskDelete(nullptr);
        return;
    }

    ESP_LOGI(kTag, "Listening for Codex status on UDP/%u", kPort);
    while (!stop_requested_.load()) {
        char packet[kMaxPacketSize + 1] = {};
        sockaddr_in sender_address = {};
        socklen_t sender_length = sizeof(sender_address);
        int received = recvfrom(socket_fd, packet, kMaxPacketSize, 0,
                                reinterpret_cast<sockaddr*>(&sender_address), &sender_length);
        if (received < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK) {
                continue;
            }
            ESP_LOGE(kTag, "recvfrom() failed: errno=%d", errno);
            break;
        }

        bool running = false;
        int active_turn_count = 0;
        int daily_tokens = 0;
        if (IsFailurePacket(packet, static_cast<size_t>(received))) {
            failure_callback_();
            continue;
        }
        if (ParseSummaryPacket(packet, static_cast<size_t>(received), daily_tokens)) {
            summary_callback_(daily_tokens);
            continue;
        }
        if (!ParsePacket(packet, static_cast<size_t>(received), running, active_turn_count)) {
            ESP_LOGW(kTag, "Rejected malformed Codex status packet (%d bytes)", received);
            continue;
        }

        host_address_.store(sender_address.sin_addr.s_addr);
        state_changed_callback_(running, active_turn_count);

        char acknowledgement[kMaxPacketSize] = {};
        int acknowledgement_length = snprintf(
            acknowledgement, sizeof(acknowledgement), "%s %s %d", kAckPrefix,
            running ? "RUNNING" : "IDLE", active_turn_count);
        if (acknowledgement_length <= 0 ||
            acknowledgement_length >= static_cast<int>(sizeof(acknowledgement))) {
            ESP_LOGE(kTag, "Failed to format acknowledgement");
            continue;
        }
        int sent = sendto(socket_fd, acknowledgement, acknowledgement_length, 0,
                          reinterpret_cast<sockaddr*>(&sender_address), sender_length);
        if (sent != acknowledgement_length) {
            ESP_LOGE(kTag, "sendto() acknowledgement failed: errno=%d", errno);
        }
    }

    close(socket_fd);
    started_.store(false);
    task_handle_ = nullptr;
    vTaskDelete(nullptr);
}

bool CodexStatusReceiver::IsFailurePacket(const char* packet, size_t length) {
    constexpr char kFailurePacket[] = "XINGZHI_CODEX_V1 FAILURE";
    return packet != nullptr && length == sizeof(kFailurePacket) - 1 &&
           std::memcmp(packet, kFailurePacket, sizeof(kFailurePacket) - 1) == 0;
}

bool CodexStatusReceiver::ParseSummaryPacket(const char* packet, size_t length,
                                             int& daily_tokens) {
    if (packet == nullptr || length == 0 || length > kMaxPacketSize ||
        std::memchr(packet, '\0', length) != nullptr) {
        return false;
    }

    char terminated_packet[kMaxPacketSize + 1] = {};
    std::memcpy(terminated_packet, packet, length);
    int parsed_tokens = -1;
    int consumed = 0;
    if (std::sscanf(terminated_packet, "XINGZHI_CODEX_V1 SUMMARY %d%n", &parsed_tokens,
                    &consumed) != 1 || consumed != static_cast<int>(length) ||
        parsed_tokens < 0) {
        return false;
    }
    daily_tokens = parsed_tokens;
    return true;
}

bool CodexStatusReceiver::ParsePacket(const char* packet, size_t length, bool& running,
                                      int& active_turn_count) {
    if (packet == nullptr || length == 0 || length > kMaxPacketSize ||
        std::memchr(packet, '\0', length) != nullptr) {
        return false;
    }

    char terminated_packet[kMaxPacketSize + 1] = {};
    std::memcpy(terminated_packet, packet, length);

    char prefix[sizeof(kProtocolPrefix)] = {};
    char status[8] = {};
    int parsed_count = -1;
    int consumed = 0;
    if (std::sscanf(terminated_packet, "%16s %7s %d%n", prefix, status, &parsed_count,
                    &consumed) != 3 ||
        consumed != static_cast<int>(length) || std::strcmp(prefix, kProtocolPrefix) != 0 ||
        parsed_count < 0 || parsed_count > 999) {
        return false;
    }

    if (std::strcmp(status, "RUNNING") == 0 && parsed_count > 0) {
        running = true;
    } else if (std::strcmp(status, "IDLE") == 0 && parsed_count == 0) {
        running = false;
    } else {
        return false;
    }
    active_turn_count = parsed_count;
    return true;
}
