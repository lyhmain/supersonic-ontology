-- ============================================================
-- supersonic-ontology 演示数据:连锁茶饮(demo_tea)
-- 用法:把 dw 替换成你的库,然后在 Doris 执行本文件
-- 之后:examples/profiles/demo_tea.json 复制到 profiles/ 即可问数
-- ============================================================

CREATE TABLE IF NOT EXISTS dw.demo_tea_store (
  id   BIGINT      NOT NULL COMMENT '门店ID',
  name VARCHAR(64) NOT NULL COMMENT '门店名',
  city VARCHAR(64)          COMMENT '城市'
) DUPLICATE KEY(id) DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1");

CREATE TABLE IF NOT EXISTS dw.demo_tea_orders (
  dt        DATE           NOT NULL COMMENT '日期',
  id        BIGINT         NOT NULL COMMENT '小票ID',
  store_id  BIGINT                  COMMENT '门店ID',
  is_refund TINYINT                 COMMENT '是否退款',
  amount    DECIMAL(12,2)           COMMENT '营业额',
  cups      INT                     COMMENT '杯数'
) DUPLICATE KEY(dt, id) DISTRIBUTED BY HASH(id) BUCKETS 1
PROPERTIES("replication_num"="1");

TRUNCATE TABLE dw.demo_tea_store;
TRUNCATE TABLE dw.demo_tea_orders;

INSERT INTO dw.demo_tea_store VALUES
 (1,'西湖店','杭州'),(2,'湖滨店','杭州'),(3,'观前店','苏州');

INSERT INTO dw.demo_tea_orders VALUES
 (DATE'2026-09-15',301,1,0,5200.00,300),     -- 周年庆(见 demo_tea.json holidays)
 (DATE'2026-09-15',302,2,0,6100.00,350),
 (DATE'2026-09-15',303,3,0,4450.00,260),
 (DATE'2026-09-29',201,1,0,3520.25,195),
 (DATE'2026-09-29',202,2,0,4310.40,249),
 (DATE'2026-09-29',203,3,0,1998.00,110),
 (DATE'2026-09-29',204,1,1,128.00,8),        -- 退款单:口径过滤排除
 (DATE'2026-09-30',101,1,0,3860.50,210),
 (DATE'2026-09-30',102,2,0,4520.00,260),
 (DATE'2026-09-30',103,3,0,2140.75,118);

-- 验收锚点(demo_tea.json acceptance 的 value 依据):
--   昨天(09-29)营业额 = 9828.65 / 杯数 = 554
--   09月营业额 = 36099.90 / 杯数 = 2052 / 杯单价 = 17.59
--   周年庆(09-15)营业额 = 15750.00 / 西湖店 = 12580.75
