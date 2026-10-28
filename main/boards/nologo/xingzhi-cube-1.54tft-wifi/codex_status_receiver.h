#ifndef CODEX_STATUS_RECEIVER_H
#define CODEX_STATUS_RECEIVER_H

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <string_view>

#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

class CodexStatusReceiver {
public:
    using StateChangedCallback = std::function<void(bool running, int active_turn_count)>;
    using SummaryCallback = std::function<void(int daily_tokens)>;
    using FailureCallback = std::function<void()>;

    CodexStatusReceiver(StateChangedCallback state_changed_callback,
                        SummaryCallback summary_callback,
                        FailureCallback failure_callback);
    ~CodexStatusReceiver();

    CodexStatusReceiver(const CodexStatusReceiver&) = delete;
    CodexStatusReceiver& operator=(const CodexStatusReceiver&) = delete;

    void Start();
    bool SendTranscript(std::string_view text) const;

private:
    static constexpr uint16_t kPort = 42154;
    static constexpr uint16_t kVoicePort = 42155;

    StateChangedCallback state_changed_callback_;
    SummaryCallback summary_callback_;
    FailureCallback failure_callback_;
    std::atomic<bool> started_{false};
    std::atomic<bool> stop_requested_{false};
    std::atomic<uint32_t> host_address_{0};
    TaskHandle_t task_handle_ = nullptr;

    static void TaskEntry(void* arg);
    void Run();
    static bool ParsePacket(const char* packet, size_t length, bool& running,
                            int& active_turn_count);
    static bool ParseSummaryPacket(const char* packet, size_t length, int& daily_tokens);
    static bool IsFailurePacket(const char* packet, size_t length);
};

#endif
