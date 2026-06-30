#!/bin/bash
# VoxCPM2 語音克隆 App 啟動檔（venv 版，給 M4 Pro 這台）
# 雙擊即可：自動進入 venv 環境，再啟動小視窗 App。

# 進入 App 所在資料夾
cd "$HOME/VoxCPM2-App"

# 進入 venv 環境
source "$HOME/VoxCPM2-App/venv/bin/activate"

# 啟動 App
python "$HOME/VoxCPM2-App/voxapp.py"
