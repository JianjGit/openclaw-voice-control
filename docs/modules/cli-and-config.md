# CLI & 配置加载流程

> 源码：`src/openclaw_voice_control/cli.py`, `config.py`

---

```
1. [@OpenclawVoiceControl] 解析命令行参数 (--config, --env-file)
2. 加载 .env 环境变量
3. 读取 YAML 配置文件，替换 ${VAR} 占位符
4. 构造 {VoiceControlConfig} 数据类（含各子配置）
5. 实例化 {VoiceControlService}，调用 run()
```

## 配置优先级

```
.env 环境变量 > YAML 文件值 > 代码默认值
```
