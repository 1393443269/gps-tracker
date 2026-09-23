# -*- coding: utf-8 -*-
"""
自动扣费自测:按到期日扣月租 → 余额不足置欠费 → 到期日顺延 → 空到期日不扣。
贴合项目自测风格,零pytest依赖,真PG跑,自动清理测试数据。

运行(后端容器内):
    docker compose exec -T backend python /app/test_billing_flow.py
返回码 0=全通过。
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import app as A
from core.db import db_query_one, db_exec

PASS = 0
FAIL = 0
PFX = '__TEST_BILLING__'


def ck(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + "  " + str(detail))


def cleanup():
    try:
        rows = db_query_one("SELECT id FROM sim_card WHERE iccid LIKE ?", (PFX + '%',))
        # 逐个删(可能多张)
        while True:
            r = db_query_one("SELECT id FROM sim_card WHERE iccid LIKE ?", (PFX + '%',))
            if not r:
                break
            db_exec("DELETE FROM recharge WHERE sim_id=?", (r['id'],))
            db_exec("DELETE FROM sim_card WHERE id=?", (r['id'],))
    except Exception as e:
        print("  [warn] cleanup: " + str(e))


def mk_sim(suffix, balance, fee, expire, status='正常'):
    db_exec(
        "INSERT INTO sim_card (iccid,balance,monthly_fee,expire_date,status,org_id) VALUES (?,?,?,?,?,?)",
        (PFX + suffix, balance, fee, expire, status, 1))
    return db_query_one("SELECT id FROM sim_card WHERE iccid=?", (PFX + suffix,))['id']


def get(sid):
    r = db_query_one("SELECT balance,expire_date,status FROM sim_card WHERE id=?", (sid,))
    # PG 返回 date 对象,SQLite 返回文本;统一成字符串便于断言
    if r and r.get('expire_date') is not None and hasattr(r['expire_date'], 'strftime'):
        r = dict(r)
        r['expire_date'] = r['expire_date'].strftime('%Y-%m-%d')
    return r


def main():
    from datetime import date, timedelta
    print("=" * 60)
    print("自动扣费自测")
    print("=" * 60)
    cleanup()

    today = date.today().strftime('%Y-%m-%d')
    yesterday = (date.today() - timedelta(days=1)).strftime('%Y-%m-%d')
    future = (date.today() + timedelta(days=40)).strftime('%Y-%m-%d')

    # 先单测日期顺延算法
    print("\n[0] 日期顺延算法 _add_one_month")
    ck("普通月 2026-03-15 → 2026-04-15", A._add_one_month('2026-03-15') == '2026-04-15',
       A._add_one_month('2026-03-15'))
    ck("跨年 2026-12-20 → 2027-01-20", A._add_one_month('2026-12-20') == '2027-01-20',
       A._add_one_month('2026-12-20'))
    ck("月末 2026-01-31 → 2026-02-28(2月无31)", A._add_one_month('2026-01-31') == '2026-02-28',
       A._add_one_month('2026-01-31'))

    # 场景A:余额够,到期日≤今天 → 扣费+顺延+负流水
    print("\n[A] 余额够(100)月租30 到期昨天 → 应扣30、余70、到期顺延、生成扣费流水")
    a = mk_sim('_A', 100, 30, yesterday)
    A.scan_and_bill_once()
    ga = get(a)
    ck("A: 余额扣到70", float(ga['balance']) == 70, "实际=" + str(ga['balance']))
    ck("A: 到期日已顺延(不再是昨天)", ga['expire_date'] != yesterday, "实际=" + str(ga['expire_date']))
    ck("A: 状态仍正常", ga['status'] == '正常', ga['status'])
    neg = db_query_one("SELECT amount,status FROM recharge WHERE sim_id=? AND status='扣费流水'", (a,))
    ck("A: 生成-30扣费流水", neg is not None and float(neg['amount']) == -30,
       str(neg['amount'] if neg else None))

    # 场景B:余额不足 → 不扣,置欠费,到期日不变
    print("\n[B] 余额10 月租30 到期昨天 → 余额不足,应置欠费、不扣、到期日不变")
    b = mk_sim('_B', 10, 30, yesterday)
    A.scan_and_bill_once()
    gb = get(b)
    ck("B: 余额未动(仍10)", float(gb['balance']) == 10, "实际=" + str(gb['balance']))
    ck("B: 状态置欠费", gb['status'] == '欠费', gb['status'])
    ck("B: 到期日不变(仍昨天)", gb['expire_date'] == yesterday, "实际=" + str(gb['expire_date']))

    # 场景C:到期日为空 → 永不扣费
    print("\n[C] 余额100 月租30 到期为空 → 应完全不扣")
    c = mk_sim('_C', 100, 30, None)
    A.scan_and_bill_once()
    gc = get(c)
    ck("C: 余额未动(仍100)", float(gc['balance']) == 100, "实际=" + str(gc['balance']))
    ck("C: 状态仍正常", gc['status'] == '正常', gc['status'])

    # 场景D:未到期(到期日在未来)→ 不扣
    print("\n[D] 余额100 月租30 到期40天后 → 未到期,应不扣")
    d = mk_sim('_D', 100, 30, future)
    A.scan_and_bill_once()
    gd = get(d)
    ck("D: 余额未动(仍100)", float(gd['balance']) == 100, "实际=" + str(gd['balance']))

    # 场景E:月租0 → 不扣
    print("\n[E] 余额100 月租0 到期昨天 → 月租0,应不扣")
    e = mk_sim('_E', 100, 0, yesterday)
    A.scan_and_bill_once()
    ge = get(e)
    ck("E: 余额未动(仍100)", float(ge['balance']) == 100, "实际=" + str(ge['balance']))

    # 场景F:幂等——A卡已扣费顺延到未来,再扫一次不应重复扣
    print("\n[F] 幂等:A卡已扣费顺延,再扫一次不应再扣")
    A.scan_and_bill_once()
    ga2 = get(a)
    ck("F: A卡余额仍70(未被重复扣)", float(ga2['balance']) == 70, "实际=" + str(ga2['balance']))

    print("\n[清理] 删除测试数据")
    cleanup()
    left = db_query_one("SELECT id FROM sim_card WHERE iccid LIKE ?", (PFX + '%',))
    ck("测试数据已清理", left is None)

    print("\n" + "=" * 60)
    print("结果:%d 通过,%d 失败" % (PASS, FAIL))
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    try:
        rc = main()
    except Exception as ex:
        import traceback
        traceback.print_exc()
        try:
            cleanup()
        except Exception:
            pass
        rc = 2
    sys.exit(rc)
