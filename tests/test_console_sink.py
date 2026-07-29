from claude_register import console


def test_log_prints_when_no_sink(capsys):
    console.log("hello")
    assert "hello" in capsys.readouterr().out


def test_log_routes_to_sink():
    lines = []
    token = console.set_sink(lines.append)
    try:
        console.log("to-sink")
        console.banner("BAN")
    finally:
        console.reset_sink(token)
    assert "to-sink" in lines
    assert any("BAN" in x for x in lines)


def test_sink_reset_restores_print(capsys):
    token = console.set_sink(lambda _: None)
    console.reset_sink(token)
    console.log("back-to-stdout")
    assert "back-to-stdout" in capsys.readouterr().out
