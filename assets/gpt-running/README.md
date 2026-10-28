# GPT Running Media Assets

Generated from the complete local source video for the Xingzhi Cube 1.54 TFT board. No portion of the 63.6-second source timeline was removed.

| File | Intended use | Format | Duration | Size | SHA-256 |
|---|---|---|---:|---:|---|
| `gpt-running.gif` | Device display loop | GIF, 240×240, 5 fps, 64 colors, 318 frames | 63.60 s | 4,560,566 B | `AB87B61182E49196EE4BCA581DC82399D0237BBBF9951DC2ED2281E2A13AAB96` |
| `gpt-running.ogg` | Device running-state music | Opus in OGG, 48 kHz, mono | 63.71 s | 259,191 B | `7DA36B1D5D0B96EB952F1826BC8C67ED4D49BCD292EDD3FE0599691DED844219` |
| `gpt-running-preview.mp4` | Desktop preview | H.264 Baseline/AAC, 240×240, 15 fps | 63.60 s | 1,327,572 B | `C5FD3D31D7BEF4A4D2BB39237327162DA7E3374718CBF424CECB22D6ED19B035` |

The GIF uses the entire video and loops only after reaching the end. Its frame rate and palette were reduced to fit embedded storage without shortening the content. The GIF plus full OGG audio and the existing generated firmware assets total 6,595,434 bytes, leaving 1,793,174 bytes in the 8 MiB assets partition.

All three files pass full FFmpeg decoding. The original source file was not modified. The superseded six-second audio excerpt was preserved in `.codex-tasks/20260928-douyin-media/raw/gpt-running-loop-6s.ogg` and is not a deliverable.

