#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VoxCPM2 語音克隆小視窗 App
功能：人物管理（新增/刪除/下拉切換）、參考音檔文字稿、進階參數、一次一句生成。
生成頁支援「臨時上傳」：當場上傳音檔即可生成，生成後可選擇是否存成人物。
介面用 Gradio，包進 pywebview 獨立視窗。
"""

import os
# 關閉 Gradio 對外的版本檢查連線（避免無網路/被擋時啟動失敗），必須在 import gradio 前設定
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import re
import json
import shutil
import socket
import datetime
import traceback
import subprocess

import numpy as np
import soundfile as sf
import gradio as gr

import updater  # 自動更新模組

# ----------------------------------------------------------------------
# 路徑設定：所有資料都放在這支程式同層的資料夾，方便備份與 GitHub 同步
# ----------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")            # 存人物清單與參考音檔
VOICES_DIR = os.path.join(DATA_DIR, "voices")       # 參考音檔複本
OUTPUT_DIR = os.path.join(APP_DIR, "outputs")       # 生成結果
PROFILES_FILE = os.path.join(DATA_DIR, "profiles.json")

for d in (DATA_DIR, VOICES_DIR, OUTPUT_DIR):
    os.makedirs(d, exist_ok=True)

# ----------------------------------------------------------------------
# 人物資料：存成 profiles.json
# 結構： { "人物名稱": {"audio": "voices/xxx.wav", "prompt_text": "參考音檔逐字稿"} }
# ----------------------------------------------------------------------
def load_profiles():
    if os.path.exists(PROFILES_FILE):
        try:
            with open(PROFILES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_profiles(profiles):
    with open(PROFILES_FILE, "w", encoding="utf-8") as f:
        json.dump(profiles, f, ensure_ascii=False, indent=2)

def profile_names():
    return list(load_profiles().keys())

def safe_filename(name):
    # 把人物名稱轉成安全的英數檔名，避免空格/中文造成路徑問題
    base = re.sub(r"[^A-Za-z0-9_-]", "_", name).strip("_")
    if not base:
        base = "voice"
    return base

def text_to_filename(text, fallback="voice", max_bytes=60):
    """把要生成的台詞轉成檔名主體：保留中英文，只移除檔名非法字元，依位元組長度截斷。"""
    text = (text or "").strip()
    text = re.sub(r"[\r\n\t]+", " ", text)          # 換行/定位 → 空白
    text = re.sub(r'[\\/:*?"<>|]', "", text)         # 移除路徑/檔名非法字元
    text = re.sub(r"\s+", "_", text).strip("_. ")    # 空白收成底線
    if not text:
        return fallback
    # 依 UTF-8 位元組截斷（中文一字約 3 bytes），避免超過檔名長度上限
    text = text.encode("utf-8")[:max_bytes].decode("utf-8", "ignore").strip("_. ")
    return text or fallback

# ----------------------------------------------------------------------
# 輸出資料夾：使用者可自訂，記在 settings.json（沿用 updater 的設定檔，更新時不會被覆蓋）
# ----------------------------------------------------------------------
def get_output_dir():
    """目前輸出資料夾：使用者有設定就用，否則用預設 outputs。一律確保資料夾存在。"""
    try:
        d = (updater.load_settings().get("output_dir") or "").strip()
    except Exception:
        d = ""
    if d:
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except Exception:
            pass  # 設定的路徑不能用（例如外接碟拔掉）→ 退回預設
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR

def set_output_dir(path):
    s = updater.load_settings()
    s["output_dir"] = path
    updater.save_settings(s)

# pywebview 視窗的全域參照（給「選擇資料夾」叫出原生對話框用；瀏覽器模式時為 None）
_webview_window = None

# ----------------------------------------------------------------------
# 模型：全域只載入一次（第一次生成時才載，省啟動時間）
# ----------------------------------------------------------------------
_model = None
_model_device = None

def get_model(device_choice):
    """依使用者選的 device 載入模型。device 改變時重新載入。"""
    global _model, _model_device
    dev = None if device_choice == "auto" else device_choice
    if _model is not None and _model_device == device_choice:
        return _model
    from voxcpm import VoxCPM
    # optimize=False 在 Apple 晶片上較穩（torch.compile 只對 CUDA 有效）
    # load_denoiser=False：不載入模型內建降噪器，省記憶體（對 8GB 機器較友善）；
    #   參考音檔的降噪改由下方「降噪」選項用 FFmpeg 處理，與此無關
    _model = VoxCPM.from_pretrained(
        hf_model_id="openbmb/VoxCPM2",
        device=dev,
        optimize=False,
        load_denoiser=False,
    )
    _model_device = device_choice
    return _model

# ----------------------------------------------------------------------
# 動作：新增人物
# ----------------------------------------------------------------------
def add_profile(name, audio_path, prompt_text):
    name = (name or "").strip()
    if not name:
        return gr.update(), "❌ 請先填人物名稱"
    if not audio_path:
        return gr.update(), "❌ 請先上傳參考音檔"

    profiles = load_profiles()
    if name in profiles:
        return gr.update(), f"❌ 人物「{name}」已存在，請換個名字或先刪除"

    # 把上傳的音檔複製進 voices 資料夾，檔名用安全字元
    ext = os.path.splitext(audio_path)[1] or ".wav"
    dest_name = safe_filename(name) + ext
    dest_path = os.path.join(VOICES_DIR, dest_name)
    shutil.copyfile(audio_path, dest_path)

    profiles[name] = {
        "audio": os.path.join("voices", dest_name),
        "prompt_text": (prompt_text or "").strip(),
    }
    save_profiles(profiles)

    names = list(profiles.keys())
    return (
        gr.update(choices=names, value=name),
        f"✅ 已新增人物「{name}」",
    )

# ----------------------------------------------------------------------
# 動作：刪除人物
# ----------------------------------------------------------------------
def delete_profile(name):
    if not name:
        return gr.update(), "❌ 沒有選擇要刪除的人物"
    profiles = load_profiles()
    if name in profiles:
        # 一併刪除音檔複本
        audio_rel = profiles[name].get("audio", "")
        audio_abs = os.path.join(DATA_DIR, audio_rel)
        if audio_rel and os.path.exists(audio_abs):
            try:
                os.remove(audio_abs)
            except OSError:
                pass
        del profiles[name]
        save_profiles(profiles)
    names = list(profiles.keys())
    new_value = names[0] if names else None
    return (
        gr.update(choices=names, value=new_value),
        f"🗑️ 已刪除人物「{name}」",
    )

# ----------------------------------------------------------------------
# 動作：選擇人物時，顯示該人物的文字稿（唯讀提示）
# ----------------------------------------------------------------------
def on_select_profile(name):
    profiles = load_profiles()
    if name and name in profiles:
        return profiles[name].get("prompt_text", "")
    return ""

# ----------------------------------------------------------------------
# 動作：生成語音
# ----------------------------------------------------------------------
def generate(source_mode, name, temp_audio, temp_prompt,
             target_text, device_choice, cfg_value, timesteps, denoise):
    """
    來源有兩種：
      - 「用現有人物」：從 profiles.json 取音檔與逐字稿
      - 「臨時上傳」  ：直接用使用者這次上傳的音檔與逐字稿，不存清單
    回傳 5 個值：
      out_path, 狀態訊息, 存檔區顯示控制, 暫存音檔路徑(State), 暫存逐字稿(State)
    （後兩個只在臨時上傳時帶值，給「存成人物」按鈕使用）
    """
    HIDE = gr.update(visible=False)
    target_text = (target_text or "").strip()
    if not target_text:
        return None, "❌ 請先輸入要生成的台詞文字", HIDE, None, None

    is_temp = (source_mode == "臨時上傳（只用這次）")
    if is_temp:
        if not temp_audio:
            return None, "❌ 臨時上傳模式：請先上傳參考音檔", HIDE, None, None
        audio_abs = temp_audio
        prompt_text_clean = (temp_prompt or "").strip() or None
    else:
        profiles = load_profiles()
        if not (name and name in profiles):
            return None, "❌ 請先在上方選擇或新增一個人物", HIDE, None, None
        audio_rel = profiles[name].get("audio", "")
        audio_abs = os.path.join(DATA_DIR, audio_rel)
        if not (audio_rel and os.path.exists(audio_abs)):
            return None, f"❌ 找不到人物「{name}」的參考音檔，請重新新增", HIDE, None, None
        prompt_text_clean = (profiles[name].get("prompt_text", "") or "").strip() or None

    try:
        model = get_model(device_choice)

        # 與官方 app.py 完全一致的組裝方式：
        # reference_wav_path 永遠帶上；若有逐字稿，再「額外疊加」prompt_wav_path + prompt_text
        kwargs = dict(
            text=target_text,
            reference_wav_path=audio_abs,
            cfg_value=float(cfg_value),
            inference_timesteps=int(timesteps),
            normalize=True,          # 對齊官方預設
            denoise=bool(denoise),
        )
        if prompt_text_clean:
            kwargs["prompt_wav_path"] = audio_abs
            kwargs["prompt_text"] = prompt_text_clean

        wav = model.generate(**kwargs)
        wav = np.asarray(wav, dtype=np.float32)

        # 取樣率一律跟模型拿。不設 fallback：
        # 萬一拿不到，寧可在這裡報錯，也不要用錯誤取樣率默默存出變調的音檔
        sr = int(model.tts_model.sample_rate)

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_name = f"{text_to_filename(target_text)}_{ts}.wav"
        out_dir = get_output_dir()
        out_path = os.path.join(out_dir, out_name)
        sf.write(out_path, wav, sr)

        status = f"✅ 生成完成：{out_name}（取樣率 {sr}Hz）\n（已存到輸出資料夾：{out_dir}）"

        if is_temp:
            # 臨時上傳成功 → 顯示「存成人物」區，並把這次用的音檔/逐字稿暫存起來
            return out_path, status, gr.update(visible=True), audio_abs, (prompt_text_clean or "")
        return out_path, status, HIDE, None, None
    except Exception as e:
        return None, "❌ 生成失敗：\n" + "".join(
            traceback.format_exception_only(type(e), e)
        ) + "\n（把這整段貼回去問就能除錯）", HIDE, None, None

# ----------------------------------------------------------------------
# 動作：切換參考來源（顯示/隱藏對應區塊，並收起存檔區）
# ----------------------------------------------------------------------
def on_toggle_source(mode):
    is_temp = (mode == "臨時上傳（只用這次）")
    return (
        gr.update(visible=not is_temp),  # existing_group
        gr.update(visible=is_temp),      # temp_group
        gr.update(visible=False),        # save_group（切換時一律收起，避免殘留）
    )

# ----------------------------------------------------------------------
# 動作：把臨時上傳的聲音存成正式人物
# 沿用 add_profile：會把音檔「複製」進 voices/、寫進 profiles.json
# （複製而非引用很重要：Gradio 暫存檔之後會被清掉，引用會壞掉）
# ----------------------------------------------------------------------
def save_temp_as_profile(name, used_audio, used_prompt):
    if not used_audio:
        return (gr.update(), gr.update(),
                "❌ 沒有可儲存的臨時音檔，請先用「臨時上傳」生成一次",
                gr.update(), gr.update())
    dd_update, msg = add_profile(name, used_audio, used_prompt or "")
    if msg.startswith("✅"):
        # 成功：兩個下拉都更新、收起存檔區、清空名稱欄
        return dd_update, dd_update, msg, gr.update(visible=False), gr.update(value="")
    # 失敗（名稱空白/重複）：保留存檔區讓使用者改
    return dd_update, dd_update, msg, gr.update(visible=True), gr.update()

# ----------------------------------------------------------------------
# 介面
# ----------------------------------------------------------------------
def build_ui():
    names = profile_names()
    init_value = names[0] if names else None

    with gr.Blocks(title="VoxCPM2 語音克隆") as demo:
        gr.Markdown("## 🎙️ VoxCPM2 語音克隆\n選擇人物 → 輸入台詞 → 生成。一次一句。")

        with gr.Tab("生成語音"):
            source_mode = gr.Radio(
                choices=["用現有人物", "臨時上傳（只用這次）"],
                value="用現有人物",
                label="參考來源",
            )

            # --- A：用現有人物（預設顯示）---
            with gr.Group(visible=True) as existing_group:
                with gr.Row():
                    profile_dd = gr.Dropdown(
                        choices=names, value=init_value,
                        label="選擇人物", scale=4,
                    )
                    refresh_btn = gr.Button("🔄 重新整理清單", scale=1)

                selected_prompt = gr.Textbox(
                    label="此人物的參考文字稿（唯讀，新增時設定）",
                    value=on_select_profile(init_value),
                    interactive=False, lines=2,
                )

            # --- B：臨時上傳（預設隱藏，選了才出現）---
            with gr.Group(visible=False) as temp_group:
                temp_audio = gr.Audio(
                    label="臨時參考音檔（建議 10–15 秒、清楚、無雜音）",
                    type="filepath",
                )
                temp_prompt = gr.Textbox(
                    label="這段音檔的逐字稿（強烈建議填，能明顯改善口音與咬字）",
                    placeholder="把臨時音檔裡實際講的話，一字不漏打進來…",
                    lines=2,
                )

            target_box = gr.Textbox(
                label="要生成的台詞（這次要它念的字）",
                placeholder="在這裡打你要它念的句子…",
                lines=3,
            )
            gen_btn = gr.Button("▶️ 生成", variant="primary")
            audio_out = gr.Audio(label="生成結果", type="filepath")
            status_out = gr.Textbox(label="狀態訊息", interactive=False, lines=3)

            # --- 輸出資料夾：可開啟、可改 ---
            with gr.Row():
                output_dir_box = gr.Textbox(
                    value=get_output_dir(),
                    label="輸出資料夾（生成的音檔存這裡）",
                    interactive=False, scale=4,
                )
                open_dir_btn = gr.Button("📂 開啟", scale=1)
                choose_dir_btn = gr.Button("選擇資料夾…", scale=1)
            output_dir_status = gr.Markdown("")

            # --- 生成後：把臨時音檔存成人物（只有用「臨時上傳」生成成功才出現）---
            with gr.Group(visible=False) as save_group:
                gr.Markdown("剛剛這個聲音要存成人物嗎？存了下次可直接從下拉選單選用；不想存就略過。")
                with gr.Row():
                    save_name = gr.Textbox(
                        label="人物名稱", placeholder="例如：旁白B", scale=4,
                    )
                    save_btn = gr.Button("💾 存成人物", variant="primary", scale=1)
                save_status = gr.Textbox(label="狀態", interactive=False)

            with gr.Accordion("⚙️ 進階設定（不懂可不動）", open=False):
                device_dd = gr.Dropdown(
                    choices=["auto", "mps", "cpu"], value="auto",
                    label="運算裝置（卡住或出錯改成 cpu）",
                )
                cfg_slider = gr.Slider(
                    0.1, 10.0, value=3.0, step=0.1,
                    label="cfg-value（語氣強度，建議 1.0–3.0）",
                )
                steps_slider = gr.Slider(
                    1, 100, value=10, step=1,
                    label="inference-timesteps（步數，建議 4–30，越高越久）",
                )
                denoise_chk = gr.Checkbox(
                    value=False, label="對參考音檔降噪（雜音多時可勾，需要 FFmpeg）",
                )

        with gr.Tab("人物管理"):
            gr.Markdown("### 新增人物")
            new_name = gr.Textbox(label="人物名稱（例如：學長）")
            new_audio = gr.Audio(
                label="上傳參考音檔（建議 10–15 秒、清楚、無雜音）",
                type="filepath",
            )
            new_prompt = gr.Textbox(
                label="參考音檔的逐字稿（強烈建議填，能明顯改善口音與咬字）",
                placeholder="把參考音檔裡實際講的話，一字不漏打進來…",
                lines=2,
            )
            add_btn = gr.Button("➕ 新增這個人物", variant="primary")
            add_status = gr.Textbox(label="狀態", interactive=False)

            gr.Markdown("### 刪除人物")
            del_dd = gr.Dropdown(choices=names, value=init_value, label="選擇要刪除的人物")
            del_btn = gr.Button("🗑️ 刪除", variant="stop")
            del_status = gr.Textbox(label="狀態", interactive=False)

        # ---------------- 事件綁定 ----------------
        # 暫存：剛剛實際用來生成的臨時音檔路徑 + 逐字稿（給「存成人物」用）
        used_audio_state = gr.State(None)
        used_prompt_state = gr.State(None)

        profile_dd.change(on_select_profile, inputs=profile_dd, outputs=selected_prompt)

        # 切換參考來源：顯示/隱藏對應區塊，並把「存成人物」收起來（避免殘留上一次的）
        source_mode.change(
            on_toggle_source,
            inputs=source_mode,
            outputs=[existing_group, temp_group, save_group],
        )

        def refresh_lists():
            names_now = profile_names()
            val = names_now[0] if names_now else None
            return gr.update(choices=names_now, value=val), gr.update(choices=names_now, value=val)
        refresh_btn.click(refresh_lists, outputs=[profile_dd, del_dd])

        # 開啟輸出資料夾（順便刷新路徑：外接碟被拔掉時會自動退回預設）
        def open_output_folder():
            folder = get_output_dir()
            try:
                subprocess.run(["open", folder], check=False)
            except Exception:
                pass
            return gr.update(value=folder)
        open_dir_btn.click(open_output_folder, outputs=[output_dir_box])

        # 選擇輸出資料夾：叫出 macOS 原生資料夾對話框（需 pywebview 視窗）
        def choose_output_folder():
            if _webview_window is None:
                return gr.update(), "⚠️ 目前是瀏覽器模式，無法叫出選擇視窗。"
            try:
                import webview
                result = _webview_window.create_file_dialog(webview.FOLDER_DIALOG)
            except Exception as e:
                return gr.update(), f"⚠️ 選擇資料夾失敗：{e}"
            if not result:
                return gr.update(), "（已取消）"
            chosen = result[0] if isinstance(result, (list, tuple)) else result
            set_output_dir(chosen)
            return gr.update(value=chosen), f"✅ 已設定輸出資料夾：{chosen}"
        choose_dir_btn.click(choose_output_folder, outputs=[output_dir_box, output_dir_status])

        gen_btn.click(
            generate,
            inputs=[source_mode, profile_dd, temp_audio, temp_prompt,
                    target_box, device_dd, cfg_slider, steps_slider, denoise_chk],
            outputs=[audio_out, status_out, save_group, used_audio_state, used_prompt_state],
        )

        # 把臨時上傳的聲音存成正式人物（沿用 add_profile：會複製音檔、寫進 profiles.json）
        save_btn.click(
            save_temp_as_profile,
            inputs=[save_name, used_audio_state, used_prompt_state],
            outputs=[profile_dd, del_dd, save_status, save_group, save_name],
        )

        def add_and_sync(name, audio, ptext):
            dd_update, msg = add_profile(name, audio, ptext)
            # 同步更新兩個下拉選單
            return dd_update, msg, dd_update
        add_btn.click(
            add_and_sync,
            inputs=[new_name, new_audio, new_prompt],
            outputs=[profile_dd, add_status, del_dd],
        )

        def del_and_sync(name):
            dd_update, msg = delete_profile(name)
            return dd_update, msg, dd_update
        del_btn.click(
            del_and_sync,
            inputs=[del_dd],
            outputs=[del_dd, del_status, profile_dd],
        )

        # ====================================================================
        # 軟體更新（放在整頁最下方）
        # ====================================================================
        with gr.Accordion("🔄 軟體更新", open=False):
            _settings = updater.load_settings()
            update_status = gr.Markdown(f"目前版本：v{updater.get_local_version()}")
            with gr.Row():
                check_btn = gr.Button("檢查更新")
                do_update_btn = gr.Button("立即更新", variant="primary", visible=False)
            auto_chk = gr.Checkbox(
                value=_settings.get("auto_update_enabled", True),
                label="自動檢查更新（開啟時與每 15 天各檢查一次）",
            )

            def on_check():
                r = updater.check_for_update()
                if r["status"] == "update":
                    return (gr.update(value=f"發現新版 v{r['remote']}（目前 v{r['local']}）。按「立即更新」下載。"),
                            gr.update(visible=True))
                elif r["status"] == "latest":
                    return (gr.update(value=f"已是最新版 v{r['local']}。"),
                            gr.update(visible=False))
                else:
                    return (gr.update(value="目前無法連線檢查（可能沒有網路），稍後再試。"),
                            gr.update(visible=False))

            def on_do_update():
                r = updater.perform_update()
                if r["ok"]:
                    return (gr.update(value="✅ 更新完成！請**關掉 App 再重新打開**讓新版生效。"),
                            gr.update(visible=False))
                else:
                    return (gr.update(value=f"⚠️ {r['error']}"),
                            gr.update(visible=True))

            def on_toggle_auto(val):
                updater.set_auto_update_enabled(val)
                return gr.update()

            check_btn.click(on_check, outputs=[update_status, do_update_btn])
            do_update_btn.click(on_do_update, outputs=[update_status, do_update_btn])
            auto_chk.change(on_toggle_auto, inputs=[auto_chk], outputs=[auto_chk])

    return demo

# ----------------------------------------------------------------------
# 啟動：優先用 pywebview 開獨立視窗；失敗則退回瀏覽器
# ----------------------------------------------------------------------
def find_free_port():
    """先綁一個可用埠再放開，回傳該埠號。比 server_port=0 穩定。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port

def set_dock_icon():
    """macOS：把正在執行的程序 Dock 圖示換成 voxcpm_icon.png（讓彈出視窗也顯示 V 圖示）。"""
    icon_path = os.path.join(APP_DIR, "voxcpm_icon.png")
    if not os.path.exists(icon_path):
        return
    try:
        from AppKit import NSApplication, NSImage
        app = NSApplication.sharedApplication()
        img = NSImage.alloc().initWithContentsOfFile_(icon_path)
        if img:
            app.setApplicationIconImage_(img)
    except Exception:
        # 設定圖示失敗不影響主功能，安靜略過
        pass

def main():
    set_dock_icon()
    # 開機自動檢查更新（受開關與 15 天節流控制；任何錯誤都不影響啟動）
    try:
        _u = updater.auto_check_on_startup()
        if _u.get("status") == "update":
            print(f"🔔 有新版 v{_u['remote']}（目前 v{_u['local']}），可在 App 最下方『軟體更新』裡更新。")
    except Exception:
        pass
    demo = build_ui()
    port = find_free_port()
    # 啟動 Gradio 本地伺服器（不開分享連結、不自動開瀏覽器）
    app, local_url, _ = demo.launch(
        server_name="127.0.0.1",
        server_port=port,      # 用先挑好的固定埠，避免自動挑埠在某些環境綁定失敗
        prevent_thread_lock=True,
        inbrowser=False,
        quiet=True,
        # 允許介面讀取家目錄與外接碟內的檔案；否則使用者把輸出資料夾設在
        # App 目錄外（如桌面）時，生成結果播放器會因安全限制讀不到檔案而報錯
        allowed_paths=[os.path.expanduser("~"), "/Volumes"],
    )
    try:
        import webview
        global _webview_window
        _webview_window = webview.create_window("VoxCPM2 語音克隆", local_url, width=900, height=820)
        webview.start()
    except Exception:
        # pywebview 起不來就退回瀏覽器，不讓使用者卡死
        print("（獨立視窗啟動失敗，改用瀏覽器開啟）")
        print("請手動打開這個網址：", local_url)
        demo.block_thread()

if __name__ == "__main__":
    main()
