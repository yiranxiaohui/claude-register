def test_create_app_importable(tmp_path):
    from server.app import create_app

    app = create_app(data_dir=tmp_path, config_path=tmp_path / "c.yaml")
    assert app is not None


def test_serve_module_has_main():
    import serve

    assert hasattr(serve, "main")
