# BitAuto 国际站车型采集与飞书同步

采集易车国际站的车系、具体配置、原文参数和车系图集，并支持同步到用户自己的飞书多维表格。
以“分站 + 来源车系 ID + 来源款型 ID”识别车辆，保留中英文原值、单位、来源与核查证据。

**这是可配置的源码工程，不包含生产车型数据、图片文件、公司飞书库、账号凭据或内部工作记录。**
目前请求层使用 **requests**，数据校验使用 **Pydantic**，HTML 解析使用 **lxml**。
本版本没有 Scrapy Spider，也没有一键初始化完整飞书库的入口。

## 安装与离线验证

要求 Python 3.12+。运行测试不需要飞书账号或访问源站。

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest -q
python -m ruff check .
```

也可用 `uv sync --extra dev`、`uv run pytest -q` 和 `uv run ruff check .`。

## 配置与运行

1. 复制 `.env.example` 为 `.env`，填写自己的配置。
2. 用自己的 `lark-cli` 身份登录，并准备拥有权限的目标 Base、车系表、配置表和分站表。
3. 在 shell 中导出配置；本程序不自动加载 `.env`。
4. 先检查 [字段约定](docs/feishu-schema.md) 和 [命令说明](docs/commands.md)，再运行会写表的命令。

```sh
set -a
. ./.env
set +a
# 采集一个分站并写入已配置的飞书表；不是 dry-run。
python -m bitauto.pipeline ae
```

所有 `BITAUTO_*` 配置见 [配置说明](docs/configuration.md)。缺少飞书配置时同步入口会停止；
缺少授权的签名材料时 API 请求会停止。仓库不提供签名材料，也不包含现成账号。
默认运行数据保存在仓库内忽略提交的 `.data/`，也可指定 `BITAUTO_DATA_DIR`。

## 功能与边界

- 分站定义：AE、SA、SG、AU、UK、MY、HK、US、Global；不同站点同名或同数字 ID 不自动合并。
- 采集：目录、在售/预售配置、双语参数；分类顺序、原文、单位及选配限定尽量保留。
- 图集：按官网分类保存 URL JSON；每车系封面与图集不冒充具体配置专属图片。
- 核查：同站同款双语快照比较；区分翻译等价、资料不足、疑似差异和身份待核。
- 同步：飞书 CLI 批量写入、字段投影分页、备份及回读；运行前需要兼容的表结构。
- 核查记录是可重新计算的本地清单，**不是实时官网监控**；原采集时间未知时不能用复核时间代替。
- 收到 401/403/429、验证码或访问限制应暂停并保留证据，不绕过访问限制。

## 目录与文件用途

```text
bitauto/                 采集、解析、核查、飞书同步及受控维护代码
  config.py              唯一的部署配置入口
  client.py              API 请求、签名、缓存与暂停处理
  pipeline.py            分站采集与同步入口（会写飞书）
  models/                分站定义与 Pydantic 数据结构
  gallery.py             车系图集 URL 采集
  sync_gallery.py        图集同步的预览、应用及回读
  ...                    其他模块逐一见文件索引
tests/                  合成样例与 mock 离线测试
docs/                   架构、配置、字段、命令及逐文件用途说明
.github/workflows/      自动离线检查
.env.example            空白配置模板，不是可用凭据
pyproject.toml          包、依赖及开发工具配置
uv.lock                 可复现依赖锁定
```

- [逐文件用途索引](docs/file-map.md)
- [配置说明](docs/configuration.md)
- [运行与维护命令](docs/commands.md)
- [飞书字段约定](docs/feishu-schema.md)
- [数据处理流程](docs/architecture.md)

普通采集入口与批量维护程序分开说明。维护程序可能删除视图或变更字段展示，不能当作安装步骤顺序执行。
仓库不提供第三方车型数据、图片的使用授权，也不附带内部交接文档。
