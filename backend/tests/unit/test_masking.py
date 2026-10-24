"""数据脱敏（mask_dataset）单元测试：手机号/身份证/邮箱字段自动脱敏。"""

from app.infra.datasource import _mask_match, _mask_value, mask_dataset


def test_mask_phone_keeps_head_tail() -> None:
    assert _mask_value("13812348000", "phone") == "138****8000"


def test_mask_id_card_keeps_first4_last2() -> None:
    assert _mask_value("110101199001011234", "id_card") == "1101***********34"


def test_mask_email_masks_local_part() -> None:
    out = _mask_value("zhangsan@example.com", "email")
    assert out.startswith("zh***@")
    assert out.endswith("example.com")


def test_mask_value_returns_non_string_unchanged() -> None:
    assert _mask_value(12345, "phone") == 12345
    assert _mask_value(None, "id_card") is None


def test_mask_match_short_phone_falls_back() -> None:
    assert _mask_match("123", "phone") == "****"


def test_mask_dataset_masks_by_column_name() -> None:
    ds = {
        "columns": ["name", "phone", "id_card", "email", "amount"],
        "rows": [
            ["张三", "13812348000", "110101199001011234", "zhang@ex.com", 100],
        ],
    }
    out = mask_dataset(ds)
    assert out["masked"] is True
    assert out["rows"][0][1] == "138****8000"          # phone 脱敏
    assert out["rows"][0][2] == "1101***********34"     # id_card 脱敏
    assert out["rows"][0][3].startswith("zh***@")       # email 脱敏
    assert out["rows"][0][0] == "张三"                   # 非敏感列不变
    assert out["rows"][0][4] == 100


def test_mask_dataset_does_not_mutate_input() -> None:
    ds = {"columns": ["phone"], "rows": [["13812348000"]]}
    mask_dataset(ds)
    assert ds["rows"][0][0] == "13812348000"  # 入参未被修改


def test_mask_dataset_no_sensitive_columns_returns_same() -> None:
    ds = {"columns": ["name", "amount"], "rows": [["张三", 100]]}
    out = mask_dataset(ds)
    assert "masked" not in out
    assert out == ds


def test_mask_dataset_empty_rows_returns_same() -> None:
    ds = {"columns": ["phone"], "rows": []}
    assert mask_dataset(ds) is ds


def test_mask_dataset_column_name_case_insensitive() -> None:
    ds = {"columns": ["Mobile", "EMAIL"], "rows": [["13911112222", "a@b.com"]]}
    out = mask_dataset(ds)
    assert out["rows"][0][0] == "139****2222"
    assert out["rows"][0][1].startswith("a***@")
