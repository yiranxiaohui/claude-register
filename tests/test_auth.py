from server import auth


def test_token_roundtrip():
    t = auth.make_token("pw", "secret")
    assert auth.verify_token(t, "pw", "secret") is True


def test_wrong_password_rejected():
    t = auth.make_token("pw", "secret")
    assert auth.verify_token(t, "different", "secret") is False


def test_tampered_token_rejected():
    assert auth.verify_token("garbage", "pw", "secret") is False


def test_passwords_match_accepts_correct_ascii_and_unicode():
    assert auth.passwords_match("pw", "pw") is True
    assert auth.passwords_match("密码🔑", "密码🔑") is True


def test_passwords_match_rejects_wrong_or_non_string():
    assert auth.passwords_match("nope", "pw") is False
    assert auth.passwords_match("密码", "pw") is False
    for bad in (None, 123, ["pw"], {"p": "pw"}):
        assert auth.passwords_match(bad, "pw") is False


def test_passwords_match_rejects_when_password_unset():
    assert auth.passwords_match("", "") is False
    assert auth.passwords_match("anything", "") is False
