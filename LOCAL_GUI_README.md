# NewAPI 本地管理界面

## 启动

Windows 可以双击 `start_local.bat`，也可以在当前目录运行：

```powershell
python .\local_app.py
```

启动后会自动打开：

```text
http://127.0.0.1:8765/
```

## 配置保存

界面里的账号、Session、用户 ID、CF Clearance 和钉钉通知配置会保存到 `local_config.json`。这个文件已加入 `.gitignore`，只保留在本机。

保存后可以直接在界面点击“立即签到”。原来的命令行脚本 `python checkin.py` 也会在没有环境变量时自动读取 `local_config.json`。

## 定时签到

在界面的“定时”页开启“每天自动签到”，选择每天执行时间后点击“保存配置”。本地服务保持运行时，会在到点后自动执行签到。

如果勾选“软件启动后补跑今天错过的任务”，当你在当天设定时间之后打开软件且今天还没定时跑过时，会自动补跑一次。
