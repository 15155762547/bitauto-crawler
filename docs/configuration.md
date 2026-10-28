# 配置

通过环境变量配置，`.env.example` 只含空白模板；程序不自动读取 `.env`。

| 环境变量 | 含义 |
|---|---|
| `BITAUTO_LARK_PROFILE` | 用户自行配置并登录的 lark-cli profile，不使用作者的默认身份 |
| `BITAUTO_BASE_TOKEN` | 用户自己的目标 Base 标识 |
| `BITAUTO_TABLE_STATIONS` | 用户自己的分站表 ID |
| `BITAUTO_TABLE_SERIES` | 用户自己的车系表 ID |
| `BITAUTO_TABLE_TRIMS` | 用户自己的配置表 ID |
| `BITAUTO_SIGNING_SECRET` | 获准使用的源站 API 签名材料；本仓库不提供 |
| `BITAUTO_DATA_DIR` | 原始响应和核查数据根目录，默认项目内 `.data/` |

签名和飞书目标互相独立。离线解析、规则测试不需要真实账号。
缺少飞书设置时，在执行 CLI 前报错；不会退回作者账号或某个内置 Base。

运行目录通常为：`raw/` 存来源响应，`audit/<批次>/` 存备份/预览/回读，
`reviews/parameter-review/` 存本地核查清单和人工处理记录。每轮正式维护使用独立审计目录。
这些目录和 `.env` 不进入 Git。勿用其他工程的生产数据目录进行试跑。
