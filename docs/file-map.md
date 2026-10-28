# 逐文件用途索引

## 源码

| 文件 | 用途 |
|---|---|
| [bitauto/__init__.py](../bitauto/__init__.py) | 包入口。 |
| [bitauto/bilingual_navigation.py](../bitauto/bilingual_navigation.py) | 双语分类显示和官方字段释义的备份、预览、应用与回读。 |
| [bitauto/build_category_sheets.py](../bitauto/build_category_sheets.py) | 分类schema和参数值索引，以及分类表构建工具；会写入，不是日常爬虫入口。 |
| [bitauto/business_views.py](../bitauto/business_views.py) | 现有业务入口整理、重复视图删除与资料说明更新；专项维护。 |
| [bitauto/category_names.py](../bitauto/category_names.py) | 21 类参数的英文源键、中英显示名及兼容解析。 |
| [bitauto/client.py](../bitauto/client.py) | API 请求签名、缓存、节流、来源访问暂停及原始响应保存。 |
| [bitauto/complete_snapshot_data.py](../bitauto/complete_snapshot_data.py) | 已存快照的可确认空白补齐和核查摘要；不覆写既有参数。 |
| [bitauto/config.py](../bitauto/config.py) | 部署配置与缺配置时的拒绝执行检查；不内置生产目标或凭据。 |
| [bitauto/crawl_global_station.py](../bitauto/crawl_global_station.py) | 独立 Global 目录/参数采集并保存原始响应；与其他分站单独处理。 |
| [bitauto/diff_review.py](../bitauto/diff_review.py) | 从已保存的同站双语快照生成核查事项及证据。 |
| [bitauto/diff_rules.py](../bitauto/diff_rules.py) | 保守双语比较规则，保留单位、测试口径与配置限定。 |
| [bitauto/enrich_names_and_covers.py](../bitauto/enrich_names_and_covers.py) | 来源身份约束下的名称补充与车系封面附件上传；会写入/上传。 |
| [bitauto/feishu_sync.py](../bitauto/feishu_sync.py) | 飞书CLI批量读写、本地核查存储适配；已去掉专用样例初始化入口。 |
| [bitauto/gallery.py](../bitauto/gallery.py) | 车系图库 HTML 分类、翻页、图片身份校验和URL采集。 |
| [bitauto/hide_review_hints.py](../bitauto/hide_review_hints.py) | 从日常视图移除技术核查提示，原核查证据继续保存；直接写入。 |
| [bitauto/internal_release.py](../bitauto/internal_release.py) | 既有发布/关联维护适配器和CLI审计封装；文件名保留兼容，内容不是交接文档。 |
| [bitauto/link_series_and_models.py](../bitauto/link_series_and_models.py) | 按稳定身份建立车系与配置关联的维护工具。 |
| [bitauto/local_diff_review.py](../bitauto/local_diff_review.py) | 本地核查清单稳定键更新、人工备注和处理结论保留。 |
| [bitauto/merge.py](../bitauto/merge.py) | 同站同款的中英文参数归并，保留未披露和冲突。 |
| [bitauto/models/__init__.py](../bitauto/models/__init__.py) | 数据结构包入口。 |
| [bitauto/models/entities.py](../bitauto/models/entities.py) | 车系、款型、参数响应与归并结果的Pydantic结构。 |
| [bitauto/models/station.py](../bitauto/models/station.py) | 九个分站入口、语言和来源国家标识定义。 |
| [bitauto/param_values.py](../bitauto/param_values.py) | 原文与嵌套选项显示、现有 select 选项处理。 |
| [bitauto/pipeline.py](../bitauto/pipeline.py) | 正式分站采集协调与飞书增量同步入口；会写入目标库。 |
| [bitauto/reorder_category_fields.py](../bitauto/reorder_category_fields.py) | 按源站参数顺序重排分类字段的专项维护。 |
| [bitauto/standardize_equipment_tags.py](../bitauto/standardize_equipment_tags.py) | 标配/选配等配置状态的存量字段维护；保留限定语义。 |
| [bitauto/sync_diff_review.py](../bitauto/sync_diff_review.py) | 本地快照核查预览/刷新入口；不恢复云端差异表。 |
| [bitauto/sync_gallery.py](../bitauto/sync_gallery.py) | 图集JSON和款型引用同步、分页缩小、预览及完整回读。 |
| [bitauto/sync_global_to_feishu.py](../bitauto/sync_global_to_feishu.py) | Global存量目录、配置与分类参数补同步工具；会写入。 |
| [bitauto/uppercase_station_codes.py](../bitauto/uppercase_station_codes.py) | 分站代码及稳定键大写化的备份、迁移和验证；含同步使用的规范化函数。 |

## 测试

全部使用合成响应、临时目录或mock，不携带真实账号或生产数据。

| 文件 | 覆盖范围 |
|---|---|
| [conftest.py](../tests/conftest.py) | 离线测试专用合成配置，禁止用作生产凭据。 |
| [test_bilingual_navigation.py](../tests/test_bilingual_navigation.py) | 验证 bilingual / navigation 对应规则与回归行为。 |
| [test_business_views.py](../tests/test_business_views.py) | 验证 business / views 对应规则与回归行为。 |
| [test_catalog_names.py](../tests/test_catalog_names.py) | 验证 catalog / names 对应规则与回归行为。 |
| [test_client_and_merge.py](../tests/test_client_and_merge.py) | 验证 client / and / merge 对应规则与回归行为。 |
| [test_complete_snapshot_data.py](../tests/test_complete_snapshot_data.py) | 验证 complete / snapshot / data 对应规则与回归行为。 |
| [test_diff_review.py](../tests/test_diff_review.py) | 验证 diff / review 对应规则与回归行为。 |
| [test_equipment_tags.py](../tests/test_equipment_tags.py) | 验证 equipment / tags 对应规则与回归行为。 |
| [test_gallery.py](../tests/test_gallery.py) | 验证 gallery 对应规则与回归行为。 |
| [test_global_prices.py](../tests/test_global_prices.py) | 验证 global / prices 对应规则与回归行为。 |
| [test_hide_review_hints.py](../tests/test_hide_review_hints.py) | 验证 hide / review / hints 对应规则与回归行为。 |
| [test_local_diff_review.py](../tests/test_local_diff_review.py) | 验证 local / diff / review 对应规则与回归行为。 |
| [test_nested_param_values.py](../tests/test_nested_param_values.py) | 验证 nested / param / values 对应规则与回归行为。 |
| [test_series_page_urls.py](../tests/test_series_page_urls.py) | 验证 series / page / urls 对应规则与回归行为。 |
| [test_station_code_case.py](../tests/test_station_code_case.py) | 验证 station / code / case 对应规则与回归行为。 |
| [test_sync_gallery.py](../tests/test_sync_gallery.py) | 验证 sync / gallery 对应规则与回归行为。 |
| [test_public_configuration.py](../tests/test_public_configuration.py) | 验证配置为空时阻止网络/CLI执行、没有生产默认值、数据目录隔离。 |

## 工程文件

| 文件/目录 | 用途 |
|---|---|
| README.md | 项目边界、安装、运行和文档总入口 |
| .env.example | 无真实值的部署配置模板 |
| pyproject.toml / uv.lock | 包元数据、依赖、开发检查及锁定 |
| .github/workflows/ci.yml | GitHub离线测试和静态检查 |
| docs/ | 面向使用者的架构、字段、配置及命令说明，不包含内部交接 |
| .data/（不提交） | 原始响应、核查清单、备份、验证和暂停标记 |
