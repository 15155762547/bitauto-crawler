# 命令与副作用

先完成依赖、环境变量及自己的目标表配置。下表是独立任务入口，不是需要全部顺序运行的安装步骤。
所有示例审计目录均为本机 `.data/`，未附生产数据。新一轮维护使用新目录，已生成计划的目录只用于恢复该轮。

| 类别 | 模块/命令 | 副作用 |
|---|---|---|
| 离线验证 | `python -m pytest -q` | 只运行 mock/合成样例 |
| 日常采集 | `python -m bitauto.pipeline ae` | 访问源站并写入已配置飞书；无 dry-run |
| Global 采集 | `python -m bitauto.crawl_global_station` | 访问源站、保存 raw；遇限制应暂停 |
| 图集小样 | `python -m bitauto.gallery --audit-dir .data/audit/gallery-example --sample` | 读取自己的车系表，访问来源页面，保存结果 |
| 图集同步预览 | `python -m bitauto.sync_gallery --audit-dir .data/audit/gallery-example` | 读取/备份和预览 |
| 图集同步应用 | 上一命令加 `--apply` | 写图集和引用并回读 |
| 快照核查 | `python -m bitauto.sync_diff_review` | 本地来源/身份数据生成复核预览，不访问源站 |
| 核查清单刷新 | 上一命令加 `--refresh` | 更新本地清单，保留人工处理记录 |
| 存量补空预览 | `python -m bitauto.complete_snapshot_data --audit-dir .data/audit/completion-example` | 读取/备份表与已有快照；不是重新爬官网 |
| 存量补空应用 | 上一命令加 `--apply` | 仅补可确认空白、更新核查结果并回读 |
| 双语分类预览 | `python -m bitauto.bilingual_navigation --audit-dir .data/audit/bilingual-example` | 读取分类 schema 和本地双语证据 |
| 双语分类应用 | 上一命令加 `--apply` | 改分类显示名称及字段说明，保留身份和值 |

## 专项维护：不要当作日常任务运行

- `business_views --audit-dir <目录>`：备份与预览；加 `--apply` 会删除已定义的重复视图、写资料说明、调整列顺序并回读。
- `hide_review_hints --audit-dir <目录>`：**直接写入**，隐藏日常视图中的核查提示并精简程序生成的说明，证据仍留本地。
- `enrich_names_and_covers --audit-dir <目录>`：**写入/上传附件**，核实名称与每车系封面身份。
- `reorder_category_fields`、`standardize_equipment_tags`、`uppercase_station_codes`：**会写字段或记录**；只用于明确需要的单独维护。
- `build_category_sheets`、`sync_global_to_feishu`、`link_series_and_models`：建分类/补同步/建关联工具；依赖自己的 schema 和存量状态，不作为普通重跑入口。
- `internal_release --views`：只调整已有入口；`internal_release` 的其他选项属于关联维护，先读源码及 `--help`。

## 访问限制

源站请求出现401/403/429或验证码时，保留暂停标记和错误响应，不换身份/代理反复重试。
飞书权限不足时检查自己的 profile 和 Base 权限，不自动换账户。本文没有承诺实时更新或自动定时监控。
