# 跨市场原币收入核对仓

这是用于本地数据接收、事实核对和列式交换的仓库服务。数据使用 DuckDB 持久化，导出为 Parquet；不需要远程数据库或运行期网络。示例数据仅为本地合成的业务记录。

## 现有业务

当前按原币汇总并提供逐单交易日折算，汇率可有多个发布时间，缺汇率显式保留空值，币种精度来自币种档案，折算采用银行家舍入。更正和退款为独立金额记录并引用原单。

`reconciliation.reconcile` 固定一次日常折算核对结果及汇率知晓范围，报告缺口数。核对不是财务批准，不产生结算凭据或跨月差额。

## 关账与重述

关账把仓库产出正式冻结为结算依据，能力集中在 `warehouse.closing`，与日常核对并存、互不影响。

- **三口径并列冻结**：每个关账版本逐单保留原币金额（`original_amount`）、交易日官方汇率折算的经营口径（`operating_*`）、所属月份月末汇率折算的结算口径（`settlement_*`）。任何一种口径都不会被另一种覆盖。
- **取整差额可核对**：币种精度取自币种档案（JPY 0 位、KWD 3 位等），汇总表同时给出逐笔取整合计（`*_rounded_sum`）、未取整汇总再取整（`*_rounded_total`）及二者之差（`*_rounding_delta`），差额恒等于两种算法的差。
- **批准版本不可变**：首次关账生成 `approved` 版本并固化数字与摘要。关账后的资料变化只能走重述：生成 `proposed` 新版本和逐单差额，已批准数字原样保留；`confirm_restatement` 确认后新版本才成为现行 `approved`，旧版本转 `superseded` 但仍可完整复现。未经确认不会替换任何已结账结果。
- **差额可追溯**：`restatement_diffs` 每条差额带原单、变更类型（汇率/金额/新增/缺口填补或新增缺口）、前后汇率标识与前后版本标识；`diff_report` 从差额一路追到原单、采用的汇率和批准版本，`entry_lineage` 从原单穿过全部版本。
- **跨月归属分清**：更正/退款的经济归属跟随原单月份（`business_period`），自身处理月另记。`cross_period_moves` 显式链接来源月与结算月，各自承担什么变化可分别核对，不通过同时修改两月总数掩盖差额。重述早月时会纳入日后到达、指向该月原单的调整。
- **缺口不默认一比一**：缺官方汇率的折算保留 NULL 并打 `*_gap` 标记，版本头与汇总都统计缺口数；补发汇率后重述会产生 `gap_filled` 差额，旧版本的缺口事实仍然保留。
- **幂等且原子**：关账、重述标识重放必须同口径，直接返回既有结果而不重复写入；重复导入、重试重述或某批中途失败都不会生成两份差额，所有写入在单一事务内完成。

## 使用

运行时支持 Python 3.11.x，独立容器使用摘要固定的 Python 3.11 镜像。`requirements.txt` 精确锁定依赖版本，`requirements.lock` 保存相同版本的 Linux CPython 3.11 wheel 哈希，容器构建时校验哈希。推荐使用下列容器方式；镜像构建时准备依赖，之后运行不联网。镜像只包含本仓库应用及依赖，不启动额外服务。

```bash
docker build -t fx-warehouse .
docker run --rm --network none fx-warehouse
```

已经准备依赖的环境中也可以直接执行：

```bash
python -m unittest discover -s tests -v
python -m compileall -q warehouse tests
python -m warehouse.cli --database /tmp/fx.duckdb load samples/delivery.json
python -m warehouse.cli --database /tmp/fx.duckdb overview
# 关账：冻结 10 月的三口径版本，汇率知晓截止 11-02
python -m warehouse.cli --database /tmp/fx.duckdb close oct-close 2026-10 --base USD --known-at 2026-11-02T00:00:00Z
# 关账后接收更正、退款或补发汇率（samples/november_update.json）
python -m warehouse.cli --database /tmp/fx.duckdb load samples/november_update.json
# 重述：生成待确认版本与逐单差额，不动已批准数字
python -m warehouse.cli --database /tmp/fx.duckdb restate oct-restate 2026-10 --base USD --known-at 2026-11-15T00:00:00Z
python -m warehouse.cli --database /tmp/fx.duckdb diffs oct-restate      # 差额追到原单、汇率、版本
python -m warehouse.cli --database /tmp/fx.duckdb confirm oct-restate    # 确认后才替换现行版本
python -m warehouse.cli --database /tmp/fx.duckdb report oct-restate     # 汇总、取整差额、跨月调整
python -m warehouse.cli --database /tmp/fx.duckdb lineage j1             # 单笔原单的版本血缘
python -m warehouse.cli --database /tmp/fx.duckdb export /tmp/fx-exports release1
```

上面的导出版本不可覆盖；再次核对应使用新的数据库路径和新的导出目录，或直接运行使用临时目录的测试。测试同时验证接收事务、业务主键冲突、批号重送、持久化、列式文件回读，以及关账三口径、取整差额、重述幂等、跨月归属、缺口跟踪和版本血缘。完整测试和样本运行通常数秒结束，没有等待外部服务的步骤。

## 数据与接口

构建所需工具和传递依赖同样精确锁定，并包含在容器依赖中。执行 `python -m build --wheel --no-isolation` 可在不下载额外构建依赖的情况下生成 wheel；SQL 表定义随包分发。

接收包包含 `batch_id` 和 `tables`。合法字段由各表契约与领域校验共同约束；同批号同内容重送不重复写入，同业务键异内容拒绝整个接收事务。已有原始行和接收回执保留，业务值更正须采用领域中允许的独立记录，不能覆写原行。

`warehouse.storage.open_store` 打开仓库，`warehouse.ingest.ingest` 接收业务包，`warehouse.queries` 提供现有事实查询与关账版本清单，`warehouse.closing` 提供 `close_period`、`restate_period`、`confirm_restatement`、`version_report`、`diff_report`、`entry_lineage`。`warehouse.export.export_tables` 固定一次读取视图并生成版本目录，业务表与关账/重述事实表一并冻结，`read_export` 校验成员文件摘要后读取。`samples/delivery.json` 与 `samples/november_update.json` 展示接收字段及关账后更正、退款、补发汇率的形态。

领域校验、事实计算与查询分别存在于同一应用的不同职责中。SQL 定义保存当前存储契约；批号回执与原始行登记属于接收事务，不是业务汇总来源。文件导出只交换业务表与关账事实，接收回执不会充当事实行。
