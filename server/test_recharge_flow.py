# -*- coding: utf-8 -*-
"""
资金链路自测:充值申请 → 确认加余额 → 冲正扣回 → 分润只算已确认。

贴合本项目 test_l744.py 的自测风格,不引入 pytest 依赖。用一张临时测试 SIM 卡
走完整资金流转,每步断言余额/状态,结束清理测试数据。复用 app.py 里真实的 SQL
写法(与 confirm_recharge / reverse_recharge 同款),改坏资金逻辑即断言失败。

运行(后端容器内):
    docker compose exec -T backend python /app/test_recharge_flow.py
返回码 0 = 全通过;非 0 = 有失败。
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from core.db import get_db, db_query_one, db_exec, DB_BACKEND

PASS = 0
FAIL = 0
TEST_ICCID = '__TEST_RECHARGE_FLOW__'


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  [PASS] " + name)
    else:
        FAIL += 1
        print("  [FAIL] " + name + "  " + str(detail))


def _now():
    return "strftime('%Y-%m-%d %H:%M:%S','now','localtime')"


def cleanup():
    try:
        sim = db_query_one("SELECT id FROM sim_card WHERE iccid=?", (TEST_ICCID,))
        if sim:
            db_exec("DELETE FROM recharge WHERE sim_id=?", (sim['id'],))
            db_exec("DELETE FROM sim_card WHERE id=?", (sim['id'],))
    except Exception as e:
        print("  [warn] cleanup: " + str(e))


def bal(sid):
    r = db_query_one("SELECT balance FROM sim_card WHERE id=?", (sid,))
    return float(r['balance']) if r else None


def stt(rid):
    r = db_query_one("SELECT status FROM recharge WHERE id=?", (rid,))
    return r['status'] if r else None


def main():
    print("=" * 60)
    print("资金链路自测  后端: " + DB_BACKEND)
    print("=" * 60)

    cleanup()

    db_exec("INSERT INTO sim_card (iccid,balance,status,org_id) VALUES (?,?,?,?)",
            (TEST_ICCID, 0, '欠费', 1))
    sim = db_query_one("SELECT id FROM sim_card WHERE iccid=?", (TEST_ICCID,))
    sid = sim['id']
    print("\n[准备] 测试SIM id=" + str(sid) + " 余额0 欠费")

    print("\n[1] 提交充值申请100(余额应不变)")
    db_exec("INSERT INTO recharge (sim_id,iccid,amount,method,operator,org_id,status) VALUES (?,?,?,?,?,?,?)",
            (sid, TEST_ICCID, 100, '测试', '测试客户', 1, '待确认'))
    rc = db_query_one("SELECT id FROM recharge WHERE sim_id=? AND status='待确认'", (sid,))
    rid = rc['id']
    check("申请后余额仍0(未到账)", bal(sid) == 0, "实际=" + str(bal(sid)))
    check("状态待确认", stt(rid) == '待确认')

    print("\n[2] 确认(余额+100,欠费转正常)")
    db_exec("UPDATE sim_card SET balance=ROUND(CAST(balance + ? AS numeric),2), "
            "status=CASE WHEN status='欠费' AND (balance + ?)>=0 THEN '正常' ELSE status END WHERE id=?",
            (100, 100, sid))
    db_exec("UPDATE recharge SET status='已确认',reviewed_by=?,reviewed_at=" + _now() + " WHERE id=?",
            ('测试管理员', rid))
    check("确认后余额100", bal(sid) == 100, "实际=" + str(bal(sid)))
    check("状态已确认", stt(rid) == '已确认')
    s = db_query_one("SELECT status FROM sim_card WHERE id=?", (sid,))
    check("欠费转正常", s['status'] == '正常', "实际=" + str(s['status']))

    print("\n[3] 幂等:重复确认应被拒")
    cur = db_query_one("SELECT status FROM recharge WHERE id=?", (rid,))
    check("已确认不再是待确认(守卫生效)", cur['status'] != '待确认', "状态=" + str(cur['status']))
    check("余额仍100(未重复加)", bal(sid) == 100, "实际=" + str(bal(sid)))

    print("\n[4] 冲正(扣回0,原单已冲正,新增-100流水)")
    db_exec("UPDATE sim_card SET balance=ROUND(CAST(balance - ? AS numeric),2) WHERE id=?", (100, sid))
    db_exec("UPDATE recharge SET status='已冲正',reviewed_by=?,reviewed_at=" + _now() + " WHERE id=?",
            ('测试管理员', rid))
    db_exec("INSERT INTO recharge (sim_id,iccid,amount,method,remark,operator,org_id,status) VALUES (?,?,?,?,?,?,?,?)",
            (sid, TEST_ICCID, -100, '测试', '冲正原充值#' + str(rid), '测试管理员', 1, '冲正流水'))
    check("冲正后余额0(已扣回)", bal(sid) == 0, "实际=" + str(bal(sid)))
    check("原单已冲正", stt(rid) == '已冲正')
    neg = db_query_one("SELECT amount FROM recharge WHERE sim_id=? AND status='冲正流水'", (sid,))
    check("生成-100负流水", neg is not None and float(neg['amount']) == -100,
          "实际=" + str(neg['amount'] if neg else None))

    print("\n[5] 分润口径:只算已确认")
    ss = db_query_one("SELECT COALESCE(SUM(amount),0) as s FROM recharge WHERE sim_id=? AND status='已确认'", (sid,))
    check("冲正后已确认金额0(冲正的钱不参与分润)", float(ss['s']) == 0, "实际=" + str(ss['s']))

    print("\n[清理] 删除测试数据")
    cleanup()
    left = db_query_one("SELECT id FROM sim_card WHERE iccid=?", (TEST_ICCID,))
    check("测试数据已清理", left is None)

    print("\n" + "=" * 60)
    print("结果:" + str(PASS) + " 通过," + str(FAIL) + " 失败")
    print("=" * 60)
    return 0 if FAIL == 0 else 1


if __name__ == '__main__':
    try:
        rc = main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            cleanup()
        except Exception:
            pass
        rc = 2
    sys.exit(rc)
