from server import auth


def test_token_roundtrip():
    t = auth.make_token("pw", "secret")
    assert auth.verify_token(t, "pw", "secret") is True


def test_wrong_password_rejected():
    t = auth.make_token("pw", "secret")
    assert auth.verify_token(t, "different", "secret") is False


def test_tampered_token_rejected():
    assert auth.verify_token("garbage", "pw", "secret") is False
