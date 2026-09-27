# 知识提炼报告 · /root/projects/sql-lineage-mvp/examples/warehouse

> **只出报告，未写入候选池**（Issue #16 的边界）。
> 候选要进知识库，得先由**人**按 `docs/collaboration/notes/knowledge.md` 的流程抽检，
> 抽检合格后再用 `POST /api/knowledge/candidates` 提交（记录模板：`templates/人工抽检记录.md`）。

## 0. 概况

- 生成时间：2026-09-27T05:20:29+00:00
- 模型（经平台模型网关）：deepseek-v4-flash
- 脚本：21/21 个解析成功，共 24 条语句
- LLM 初筛调用：24 次
- 候选：51 条（通过校验 50 条 / 有问题 1 条）

## 1. 解析结果（内核 `/parse`）

| 脚本 | 语句 | 表 | 字段 | 备注 |
| --- | --- | --- | --- | --- |
| `examples/warehouse/ads/ads_产销存月报.sql` | 1 | 4 | 11 |  |
| `examples/warehouse/ads/ads_烟叶供应商排名.sql` | 1 | 2 | 9 |  |
| `examples/warehouse/ads/ads_税利分析.sql` | 1 | 4 | 10 |  |
| `examples/warehouse/ads/ads_经营指标驾驶舱.sql` | 2 | 6 | 13 |  |
| `examples/warehouse/ads/ads_设备运行看板.sql` | 1 | 2 | 8 |  |
| `examples/warehouse/cdw/dwd_卷烟产量明细.sql` | 1 | 4 | 12 |  |
| `examples/warehouse/cdw/dwd_卷烟销量明细.sql` | 1 | 3 | 9 |  |
| `examples/warehouse/cdw/dwd_成品库存明细.sql` | 1 | 3 | 9 |  |
| `examples/warehouse/cdw/dwd_烟叶采购明细.sql` | 1 | 3 | 10 |  |
| `examples/warehouse/cdw/dwd_税利明细.sql` | 1 | 3 | 9 |  |
| `examples/warehouse/cdw/dwd_设备运行明细.sql` | 1 | 3 | 11 |  |
| `examples/warehouse/cdw/dws_产销存汇总.sql` | 3 | 6 | 12 |  |
| `examples/warehouse/cdw/dws_烟叶采购供应商汇总.sql` | 1 | 2 | 8 |  |
| `examples/warehouse/cdw/dws_税利汇总.sql` | 1 | 3 | 8 |  |
| `examples/warehouse/cdw/dws_设备效率汇总.sql` | 1 | 2 | 9 |  |
| `examples/warehouse/ods/ods_卷烟产量流水.sql` | 1 | 2 | 8 |  |
| `examples/warehouse/ods/ods_卷烟销量流水.sql` | 1 | 2 | 8 |  |
| `examples/warehouse/ods/ods_成品库存快照.sql` | 1 | 2 | 8 |  |
| `examples/warehouse/ods/ods_烟叶采购到货.sql` | 1 | 2 | 8 |  |
| `examples/warehouse/ods/ods_税利上缴流水.sql` | 1 | 2 | 8 |  |
| `examples/warehouse/ods/ods_设备运行工况.sql` | 1 | 2 | 8 |  |

## 2. 通过校验的候选（50 条）

#### `ads.ads_产销存月报.sale_output_ratio`

- 中文名：销产比
- 公式：销产比 = ROUND(销量 / NULLIF(产量, 0), 4)
- 依赖字段：`cdw.dws_产销存汇总.sale_qty`、`cdw.dws_产销存汇总.output_qty`
- 来源：`examples/warehouse/ads/ads_产销存月报.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对销量与产量做了除法计算，并用 NULLIF 处理产量为 0 的情况、ROUND 保留 4 位，属于有业务含义的派生比率指标，可被下游复用。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_产销存月报.stock_increase`

- 中文名：库存增量
- 公式：库存增量 = 产量 - 销量
- 依赖字段：`cdw.dws_产销存汇总.output_qty`、`cdw.dws_产销存汇总.sale_qty`
- 来源：`examples/warehouse/ads/ads_产销存月报.sql` 第 1 条语句
- 置信度（模型自评）：**0.85**
- 模型理由：由产量与销量相减得到，是对产销存关系中库存变化的口径化计算，非单纯字段搬运，可被下游复用。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_烟叶供应商排名.amount_rank`

- 中文名：供应商采购金额排名
- 公式：amount_rank = 按 total_amt 降序对供应商行号排名（ROW_NUMBER）
- 依赖字段：`ads.ads_烟叶供应商排名.total_amt`
- 来源：`examples/warehouse/ads/ads_烟叶供应商排名.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：该字段由 ROW_NUMBER() OVER (ORDER BY c.total_amt DESC) 计算得到，是对采购金额的排序排名，属可复用的派生指标。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_烟叶供应商排名.weight_rank`

- 中文名：供应商采购重量排名
- 公式：weight_rank = 按 total_weight 降序对供应商密集排名（DENSE_RANK）
- 依赖字段：`ads.ads_烟叶供应商排名.total_weight`
- 来源：`examples/warehouse/ads/ads_烟叶供应商排名.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：该字段由 DENSE_RANK() OVER (ORDER BY c.total_weight DESC) 计算得到，是对采购重量的密集排名，属可复用的派生指标。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_税利分析.tax_profit_per_sale_box`

- 中文名：单箱销量税利
- 公式：单箱销量税利 = ROUND(税利总额 / 销量, 2)，销量为 0 时取 0
- 依赖字段：`cdw.dws_税利汇总.total_tax_profit`、`cdw.dws_产销存汇总.sale_qty`
- 来源：`examples/warehouse/ads/ads_税利分析.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：该字段由 CASE + ROUND + 除法计算得出，且已有中文业务含义的单箱税利，可被下游指标（如 ads.ads_税利分析、ads.ads_经营指标明细）复用，属于典型派生指标口径。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_税利分析.sale_qty`

- 中文名：销量
- 公式：销量 = COALESCE(产销存销量, 0)，即缺失销量按 0 处理
- 依赖字段：`cdw.dws_产销存汇总.sale_qty`
- 来源：`examples/warehouse/ads/ads_税利分析.sql` 第 1 条语句
- 置信度（模型自评）：**0.50**
- 模型理由：用 COALESCE 对销量做了空值填充，直接影响单箱销量税利的分母口径；但本质仍是字段搬运加空值处理，仅作为口径补充候选。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_经营指标驾驶舱.output_qty`

- 中文名：工厂产量合计
- 公式：output_qty = SUM(经营指标明细.output_qty)，按工厂分组
- 依赖字段：`ads.ads_经营指标明细.output_qty`
- 来源：`examples/warehouse/ads/ads_经营指标驾驶舱.sql` 第 2 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对明细产量做了 SUM 聚合，并按 plant_code、plant_name 分组，是可被下游复用的工厂级派生指标
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_经营指标驾驶舱.sale_qty`

- 中文名：工厂销量合计
- 公式：sale_qty = SUM(经营指标明细.sale_qty)，按工厂分组
- 依赖字段：`ads.ads_经营指标明细.sale_qty`
- 来源：`examples/warehouse/ads/ads_经营指标驾驶舱.sql` 第 2 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对明细销量做了 SUM 聚合，按工厂分组，属于可复用的工厂级派生指标
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_经营指标驾驶舱.sale_amt`

- 中文名：工厂销售额合计
- 公式：sale_amt = SUM(经营指标明细.sale_amt)，按工厂分组
- 依赖字段：`ads.ads_经营指标明细.sale_amt`
- 来源：`examples/warehouse/ads/ads_经营指标驾驶舱.sql` 第 2 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对明细销售额做了 SUM 聚合，按工厂分组，是可复用的工厂级派生指标
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_经营指标驾驶舱.tax_profit`

- 中文名：工厂税利合计
- 公式：tax_profit = SUM(经营指标明细.tax_profit)，按工厂分组
- 依赖字段：`ads.ads_经营指标明细.tax_profit`
- 来源：`examples/warehouse/ads/ads_经营指标驾驶舱.sql` 第 2 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对明细税利做了 SUM 聚合，按工厂分组，属于可复用的工厂级派生指标
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_经营指标驾驶舱.avg_sale_output_ratio`

- 中文名：工厂平均产销率
- 公式：avg_sale_output_ratio = AVG(经营指标明细.sale_output_ratio) 后四舍五入到固定小数位，按工厂分组
- 依赖字段：`ads.ads_经营指标明细.sale_output_ratio`
- 来源：`examples/warehouse/ads/ads_经营指标驾驶舱.sql` 第 2 条语句
- 置信度（模型自评）：**0.85**
- 模型理由：对产销率做了 AVG 聚合并套用 ROUND，是比率类派生指标，需固定口径
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_经营指标驾驶舱.max_supplier_amt`

- 中文名：供应商采购额最大值
- 公式：max_supplier_amt = MAX(烟叶供应商排名.total_amt)，按工厂分组
- 依赖字段：`ads.ads_烟叶供应商排名.total_amt`
- 来源：`examples/warehouse/ads/ads_经营指标驾驶舱.sql` 第 2 条语句
- 置信度（模型自评）：**0.70**
- 模型理由：对供应商排名的 total_amt 做了 MAX 聚合，属派生指标；口径含义依赖与经营指标明细的关联，建议明确后再固化
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_经营指标驾驶舱.max_device_run_rate`

- 中文名：设备运行率最大值
- 公式：max_device_run_rate = MAX(设备运行看板.avg_run_rate)，按工厂分组
- 依赖字段：`ads.ads_设备运行看板.avg_run_rate`
- 来源：`examples/warehouse/ads/ads_经营指标驾驶舱.sql` 第 2 条语句
- 置信度（模型自评）：**0.70**
- 模型理由：对设备运行看板的 avg_run_rate 做了 MAX 聚合，属派生指标；与工厂级分组口径需固化后复用
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_设备运行看板.device_cnt`

- 中文名：设备数量
- 公式：设备数量 = 按工厂与设备类型分组后的记录数计数
- 依赖字段：`cdw.dws_设备效率汇总.plant_code`、`cdw.dws_设备效率汇总.device_type`
- 来源：`examples/warehouse/ads/ads_设备运行看板.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对来源表做 COUNT(1) 聚合，并按 plant_code、device_type 分组，属于可被下游复用的派生指标。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_设备运行看板.run_minutes`

- 中文名：运行时长
- 公式：运行时长 = 运行分钟数求和
- 依赖字段：`cdw.dws_设备效率汇总.total_run_minutes`
- 来源：`examples/warehouse/ads/ads_设备运行看板.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对 total_run_minutes 做 SUM 聚合，是设备运行看板的核心汇总指标。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_设备运行看板.stop_minutes`

- 中文名：停机时长
- 公式：停机时长 = 停机分钟数求和
- 依赖字段：`cdw.dws_设备效率汇总.total_stop_minutes`
- 来源：`examples/warehouse/ads/ads_设备运行看板.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对 total_stop_minutes 做 SUM 聚合，是设备运行看板的核心汇总指标。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_设备运行看板.output_qty`

- 中文名：产量
- 公式：产量 = 总产出数量求和
- 依赖字段：`cdw.dws_设备效率汇总.total_output_qty`
- 来源：`examples/warehouse/ads/ads_设备运行看板.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对 total_output_qty 做 SUM 聚合，是设备产出汇总指标。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_设备运行看板.avg_run_rate`

- 中文名：平均运行率
- 公式：平均运行率 = 运行率的平均值并做四舍五入处理
- 依赖字段：`cdw.dws_设备效率汇总.run_rate`
- 来源：`examples/warehouse/ads/ads_设备运行看板.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对 run_rate 做 AVG 并套 ROUND，属于有业务含义的派生指标，可被下游复用。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `ads.ads_设备运行看板.low_run_rate_device_cnt`

- 中文名：低运行率设备数
- 公式：低运行率设备数 = 运行率低于阈值的设备计数求和
- 依赖字段：`cdw.dws_设备效率汇总.run_rate`
- 来源：`examples/warehouse/ads/ads_设备运行看板.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对 run_rate 做 CASE WHEN 条件计数并 SUM，是明确的口径型派生指标。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dwd_卷烟产量明细.output_qty_cig`

- 中文名：卷烟支数产量
- 公式：卷烟支数产量 = 卷烟产量 × 250
- 依赖字段：`ods.ods_卷烟产量流水.output_qty`
- 来源：`examples/warehouse/cdw/dwd_卷烟产量明细.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对 output_qty 做了乘法计算（p.output_qty * 250）生成新字段，属于跨单位换算的派生指标，下游可复用
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dwd_卷烟产量明细.defect_rate`

- 中文名：卷烟缺陷率
- 公式：卷烟缺陷率 = 缺陷量 / 卷烟产量（产量>0 时，ROUND 保留 6 位；产量≤0 时取 0）
- 依赖字段：`ods.ods_卷烟产量流水.defect_qty`、`ods.ods_卷烟产量流水.output_qty`
- 来源：`examples/warehouse/cdw/dwd_卷烟产量明细.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：使用 CASE WHEN + ROUND + 除法构造的比率型派生指标，含除零保护，属于有业务含义、可被下游复用的口径
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dwd_卷烟销量明细.unit_price`

- 中文名：销售单价
- 公式：销售单价 = 销售额 / 销量（销量大于零时按分单位四舍五入，销量不大于零时取零）
- 依赖字段：`ods.ods_卷烟销量流水.sale_amt`、`ods.ods_卷烟销量流水.sale_qty`
- 来源：`examples/warehouse/cdw/dwd_卷烟销量明细.sql` 第 1 条语句
- 置信度（模型自评）：**0.92**
- 模型理由：该字段不是字段搬运，而是对 ods.ods_卷烟销量流水 的 sale_amt 与 sale_qty 做除法并带 CASE 除零保护与 ROUND 取整后落到 cdw.dwd_卷烟销量明细，属于可被下游复用的派生指标，且可用字段清单中已存在 unit_price / avg_unit_price，具备口径沉淀价值。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dwd_成品库存明细.stock_unit_cost`

- 中文名：库存单位成本
- 公式：库存单位成本 = ROUND(库存金额 / NULLIF(库存数量, 0), 2)
- 依赖字段：`ods.ods_成品库存快照.stock_amt`、`ods.ods_成品库存快照.stock_qty`
- 来源：`examples/warehouse/cdw/dwd_成品库存明细.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：该字段由 stock_amt 与 stock_qty 相除并用 NULLIF 防零、ROUND 保留两位计算得出，属于派生计算字段，可被下游库存相关指标复用。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dwd_烟叶采购明细.total_amt`

- 中文名：采购总金额
- 公式：采购总金额 = ROUND(烟叶重量 × 单价, 2)
- 依赖字段：`ods.ods_烟叶采购到货.leaf_weight`、`ods.ods_烟叶采购到货.unit_price`
- 来源：`examples/warehouse/cdw/dwd_烟叶采购明细.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：该字段由 leaf_weight 与 unit_price 相乘并用 ROUND 保留两位小数计算得出，是对指标做计算加工的派生字段，具有业务含义（采购金额），可被下游复用。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dwd_税利明细.total_tax_profit`

- 中文名：税利合计
- 公式：税利合计 = 上缴税额 + 利润额
- 依赖字段：`ods.ods_税利上缴流水.tax_amt`、`ods.ods_税利上缴流水.profit_amt`
- 来源：`examples/warehouse/cdw/dwd_税利明细.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：该字段不是字段搬运，而是对 tax_amt 与 profit_amt 做加法计算得到的派生指标，具有业务含义（税利合计），可被 cdw.dws_税利汇总 等下游复用。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dwd_设备运行明细.total_minutes`

- 中文名：设备总运行分钟数
- 公式：总运行分钟数 = 运行分钟数 + 停机分钟数
- 依赖字段：`ods.ods_设备运行工况.run_minutes`、`ods.ods_设备运行工况.stop_minutes`
- 来源：`examples/warehouse/cdw/dwd_设备运行明细.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：目标表 field total_minutes 由 run_minutes + stop_minutes 相加得出，属派生计算字段，可被下游设备效率类汇总复用。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dwd_设备运行明细.hourly_output`

- 中文名：台时产量
- 公式：台时产量 = 产量 × 60 ÷ 运行分钟数（运行分钟数为 0 时取 0，结果四舍五入保留 2 位）
- 依赖字段：`ods.ods_设备运行工况.output_qty`、`ods.ods_设备运行工况.run_minutes`
- 来源：`examples/warehouse/cdw/dwd_设备运行明细.sql` 第 1 条语句
- 置信度（模型自评）：**0.95**
- 模型理由：目标字段 hourly_output 使用 CASE WHEN run_minutes > 0 结合 ROUND(output_qty * 60 / run_minutes, 2)，含除零保护与精度处理，是有业务含义的派生指标，值得固化为口径。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_产量汇总.total_output_qty`

- 中文名：总产量
- 公式：总产量 = 按 plant_code、brand_code 分组对 cdw.dwd_卷烟产量明细.output_qty 求和
- 依赖字段：`cdw.dwd_卷烟产量明细.output_qty`
- 来源：`examples/warehouse/cdw/dws_产销存汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.95**
- 模型理由：对 output_qty 做了 SUM 聚合，按 plant_code、brand_code 粒度落库，是可被下游复用的派生指标
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_产量汇总.total_defect_qty`

- 中文名：总次品量
- 公式：总次品量 = 按 plant_code、brand_code 分组对 cdw.dwd_卷烟产量明细.defect_qty 求和
- 依赖字段：`cdw.dwd_卷烟产量明细.defect_qty`
- 来源：`examples/warehouse/cdw/dws_产销存汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.95**
- 模型理由：对 defect_qty 做了 SUM 聚合，按 plant_code、brand_code 粒度落库，可与总产量配合计算缺陷率等指标
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_库存汇总.total_stock_qty`

- 中文名：库存总量
- 公式：库存总量 = 按工厂+品牌分组的成品库存明细 stock_qty 求和
- 依赖字段：`cdw.dwd_成品库存明细.stock_qty`
- 来源：`examples/warehouse/cdw/dws_产销存汇总.sql` 第 2 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：该字段由 SUM(k.stock_qty) 聚合而来，是可被下游复用的库存数量派生指标。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_库存汇总.total_stock_amt`

- 中文名：库存总金额
- 公式：库存总金额 = 按工厂+品牌分组的成品库存明细 stock_amt 求和
- 依赖字段：`cdw.dwd_成品库存明细.stock_amt`
- 来源：`examples/warehouse/cdw/dws_产销存汇总.sql` 第 2 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：该字段由 SUM(k.stock_amt) 聚合而来，是可被下游复用的库存金额派生指标。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_库存汇总.batch_cnt`

- 中文名：批次数
- 公式：批次数 = 按工厂+品牌分组对成品库存明细 batch_no 去重计数
- 依赖字段：`cdw.dwd_成品库存明细.batch_no`
- 来源：`examples/warehouse/cdw/dws_产销存汇总.sql` 第 2 条语句
- 置信度（模型自评）：**0.85**
- 模型理由：该字段由 COUNT(DISTINCT k.batch_no) 去重计数而来，具有业务含义可复用。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_产销存汇总.sale_qty`

- 中文名：销量
- 公式：销量 = SUM(卷烟销量明细.sale_qty)，按 plant_code、brand_code 汇总，无销量时取 0
- 依赖字段：`cdw.dwd_卷烟销量明细.sale_qty`、`cdw.dwd_卷烟销量明细.plant_code`、`cdw.dwd_卷烟销量明细.brand_code`
- 来源：`examples/warehouse/cdw/dws_产销存汇总.sql` 第 3 条语句
- 置信度（模型自评）：**0.85**
- 模型理由：对 cdw.dwd_卷烟销量明细 的 sale_qty 做了 SUM 聚合并按 plant_code、brand_code 分组，再用 COALESCE 补 0，是产销存口径中的核心派生指标，可被下游复用。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_产销存汇总.sale_amt`

- 中文名：销售额
- 公式：销售额 = SUM(卷烟销量明细.sale_amt)，按 plant_code、brand_code 汇总，无数据时取 0
- 依赖字段：`cdw.dwd_卷烟销量明细.sale_amt`、`cdw.dwd_卷烟销量明细.plant_code`、`cdw.dwd_卷烟销量明细.brand_code`
- 来源：`examples/warehouse/cdw/dws_产销存汇总.sql` 第 3 条语句
- 置信度（模型自评）：**0.85**
- 模型理由：对 cdw.dwd_卷烟销量明细 的 sale_amt 做了 SUM 聚合并按 plant_code、brand_code 分组，再用 COALESCE 补 0，与 sale_qty 同源成对出现，属可复用派生指标。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_产销存汇总.stock_qty`

- 中文名：库存量
- 公式：库存量 = 库存汇总.total_stock_qty，按 plant_code、brand_code 对齐，缺失时取 0
- 依赖字段：`cdw.dws_库存汇总.total_stock_qty`、`cdw.dws_库存汇总.plant_code`、`cdw.dws_库存汇总.brand_code`
- 来源：`examples/warehouse/cdw/dws_产销存汇总.sql` 第 3 条语句
- 置信度（模型自评）：**0.60**
- 模型理由：来自 cdw.dws_库存汇总 的 total_stock_qty，经 COALESCE 补 0 后按 plant_code、brand_code 落到产销存主体，属于跨表对齐后的口径约定；但本语句未对其做二次计算。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_烟叶采购供应商汇总.purchase_cnt`

- 中文名：采购笔数
- 公式：采购笔数 = 按供应商分组的采购明细记录数（COUNT(1)）
- 依赖字段：`cdw.dwd_烟叶采购明细.supplier_code`
- 来源：`examples/warehouse/cdw/dws_烟叶采购供应商汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.85**
- 模型理由：对明细做了 COUNT(1) 聚合，按 supplier_code/supplier_name/supplier_level 分组，是可复用的供应商采购频次口径
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_烟叶采购供应商汇总.total_weight`

- 中文名：采购总重量
- 公式：采购总重量 = SUM(烟叶重量)，按供应商分组汇总
- 依赖字段：`cdw.dwd_烟叶采购明细.leaf_weight`
- 来源：`examples/warehouse/cdw/dws_烟叶采购供应商汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对 leaf_weight 做了 SUM 聚合，输出字段名为 total_weight，属供应商维度派生指标
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_烟叶采购供应商汇总.total_amt`

- 中文名：采购总金额
- 公式：采购总金额 = SUM(采购金额)，按供应商分组汇总
- 依赖字段：`cdw.dwd_烟叶采购明细.total_amt`
- 来源：`examples/warehouse/cdw/dws_烟叶采购供应商汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对 total_amt 做了 SUM 聚合，是供应商采购金额口径
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_烟叶采购供应商汇总.avg_unit_price`

- 中文名：平均采购单价
- 公式：平均采购单价 = ROUND(AVG(单价), 2)，按供应商分组取均值并保留两位小数
- 依赖字段：`cdw.dwd_烟叶采购明细.unit_price`
- 来源：`examples/warehouse/cdw/dws_烟叶采购供应商汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：对 unit_price 做了 AVG 并 ROUND 到 2 位，属于计算型派生指标
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_烟叶采购供应商汇总.best_leaf_grade`

- 中文名：最优烟叶等级
- 公式：最优烟叶等级 = MAX(烟叶等级)，按供应商分组取最大值
- 依赖字段：`cdw.dwd_烟叶采购明细.leaf_grade`
- 来源：`examples/warehouse/cdw/dws_烟叶采购供应商汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.60**
- 模型理由：对 leaf_grade 做了 MAX 聚合，输出名 best_leaf_grade 带业务含义，但等级取 MAX 的业务解释需确认
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_税利汇总.tax_profit_per_box`

- 中文名：单箱税利
- 公式：单箱税利 = 当产量 > 0 时，ROUND(SUM(税利明细.total_tax_profit) / MAX(产量汇总.total_output_qty), 2)，否则取 0
- 依赖字段：`cdw.dwd_税利明细.total_tax_profit`、`cdw.dws_产量汇总.total_output_qty`
- 来源：`examples/warehouse/cdw/dws_税利汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.95**
- 模型理由：该字段由 SUM 聚合值除以 MAX 聚合值并 ROUND 取两位、且用 CASE WHEN 处理分母为 0 的边界，属于典型派生比率指标，可被下游税利分析复用。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_税利汇总.total_tax_profit`

- 中文名：税利总额
- 公式：税利总额 = SUM(税利明细.total_tax_profit)
- 依赖字段：`cdw.dwd_税利明细.total_tax_profit`
- 来源：`examples/warehouse/cdw/dws_税利汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.80**
- 模型理由：对税利明细的税利字段做 SUM 聚合，是单箱税利的分子，属于可复用的汇总指标。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_税利汇总.total_tax_amt`

- 中文名：税金总额
- 公式：税金总额 = SUM(税利明细.tax_amt)
- 依赖字段：`cdw.dwd_税利明细.tax_amt`
- 来源：`examples/warehouse/cdw/dws_税利汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.70**
- 模型理由：对税利明细税金字段做 SUM 聚合形成月度汇总指标，口径需固定避免下游重复定义。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_税利汇总.total_profit_amt`

- 中文名：利润总额
- 公式：利润总额 = SUM(税利明细.profit_amt)
- 依赖字段：`cdw.dwd_税利明细.profit_amt`
- 来源：`examples/warehouse/cdw/dws_税利汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.70**
- 模型理由：对税利明细利润字段做 SUM 聚合形成月度汇总指标，属于可复用的汇总口径。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_税利汇总.total_output_qty`

- 中文名：总产量
- 公式：总产量 = MAX(产量汇总.total_output_qty)
- 依赖字段：`cdw.dws_产量汇总.total_output_qty`
- 来源：`examples/warehouse/cdw/dws_税利汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.65**
- 模型理由：取自产量汇总并按厂牌汇总取 MAX，是单箱税利的分母，需要明确其取值口径以免下游误用为 SUM。
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_设备效率汇总.total_run_minutes`

- 中文名：累计运行时长
- 公式：累计运行时长 = 按设备分组对 run_minutes 求和
- 依赖字段：`cdw.dwd_设备运行明细.run_minutes`
- 来源：`examples/warehouse/cdw/dws_设备效率汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.82**
- 模型理由：对明细表的 run_minutes 做了 SUM 聚合，落成设备粒度的累计运行时长，是运行率的分子，可被下游复用
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_设备效率汇总.total_stop_minutes`

- 中文名：累计停机时长
- 公式：累计停机时长 = 按设备分组对 stop_minutes 求和
- 依赖字段：`cdw.dwd_设备运行明细.stop_minutes`
- 来源：`examples/warehouse/cdw/dws_设备效率汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.82**
- 模型理由：对明细表的 stop_minutes 做了 SUM 聚合，落成设备粒度的累计停机时长，是运行率的分母组成部分
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_设备效率汇总.total_output_qty`

- 中文名：累计产量
- 公式：累计产量 = 按设备分组对 output_qty 求和
- 依赖字段：`cdw.dwd_设备运行明细.output_qty`
- 来源：`examples/warehouse/cdw/dws_设备效率汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.78**
- 模型理由：对明细表的 output_qty 做了 SUM 聚合，得到设备粒度累计产量，属于可复用的产出类派生指标
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_设备效率汇总.avg_hourly_output`

- 中文名：平均小时产量
- 公式：平均小时产量 = 按设备分组对 hourly_output 求平均后做四舍五入
- 依赖字段：`cdw.dwd_设备运行明细.hourly_output`
- 来源：`examples/warehouse/cdw/dws_设备效率汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.80**
- 模型理由：对 hourly_output 做了 AVG 聚合并做 ROUND 处理，是设备效率的平均能力口径，可被下游报表复用
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

#### `cdw.dws_设备效率汇总.run_rate`

- 中文名：运行率
- 公式：运行率 = 累计运行时长 / (累计运行时长 + 累计停机时长)，分母为零时取零
- 依赖字段：`cdw.dwd_设备运行明细.run_minutes`、`cdw.dwd_设备运行明细.stop_minutes`
- 来源：`examples/warehouse/cdw/dws_设备效率汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.90**
- 模型理由：用 CASE 处理除零并做 ROUND，按设备粒度把累计运行时长与累计停机时长合成为比率型效率指标，业务含义明确且下游复用价值高
- 校验：**通过**（契约齐全 + 表/字段/数字都在解析结果里）

## 3. 需要人看的问题候选（1 条）

#### `cdw.dws_产量汇总.work_order_cnt`

- 中文名：工单数
- 公式：工单数 = 按 plant_code、brand_code 分组统计 cdw.dwd_卷烟产量明细 的行数（COUNT(1)）
- 依赖字段：（无）
- 来源：`examples/warehouse/cdw/dws_产销存汇总.sql` 第 1 条语句
- 置信度（模型自评）：**0.70**
- 模型理由：COUNT(1) 计数聚合，按 plant_code、brand_code 分组，是产量明细的计数类派生指标；计数按行进行，未引用具体字段
- 校验：**有问题（未通过）**
    - 契约：`missing_depends_on` 缺依赖字段（至少 1 条）

## 4. 人工抽检建议（10 条）

抽检规则：置信度**最低**的几条 + 最高的几条 + 随机几条（固定种子，报告可复现）。
抽检结论请记到 `templates/人工抽检记录.md`，**合格之前不要提交候选**。

| 候选 | 置信度 | 来源 | 抽检结论（待人填） | 理由（待人填） |
| --- | --- | --- | --- | --- |
| `ads.ads_税利分析.sale_qty` | 0.50 | `examples/warehouse/ads/ads_税利分析.sql` #1 | | |
| `cdw.dws_产销存汇总.stock_qty` | 0.60 | `examples/warehouse/cdw/dws_产销存汇总.sql` #3 | | |
| `cdw.dws_烟叶采购供应商汇总.best_leaf_grade` | 0.60 | `examples/warehouse/cdw/dws_烟叶采购供应商汇总.sql` #1 | | |
| `cdw.dws_产销存汇总.sale_amt` | 0.85 | `examples/warehouse/cdw/dws_产销存汇总.sql` #3 | | |
| `cdw.dwd_卷烟产量明细.output_qty_cig` | 0.90 | `examples/warehouse/cdw/dwd_卷烟产量明细.sql` #1 | | |
| `ads.ads_税利分析.tax_profit_per_sale_box` | 0.90 | `examples/warehouse/ads/ads_税利分析.sql` #1 | | |
| `cdw.dws_税利汇总.total_tax_profit` | 0.80 | `examples/warehouse/cdw/dws_税利汇总.sql` #1 | | |
| `cdw.dws_产量汇总.total_output_qty` | 0.95 | `examples/warehouse/cdw/dws_产销存汇总.sql` #1 | | |
| `cdw.dws_产量汇总.total_defect_qty` | 0.95 | `examples/warehouse/cdw/dws_产销存汇总.sql` #1 | | |
| `cdw.dws_税利汇总.tax_profit_per_box` | 0.95 | `examples/warehouse/cdw/dws_税利汇总.sql` #1 | | |
