import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

TEMP_MEDIA = tempfile.TemporaryDirectory()
os.environ["PIMEDIA_DIR"] = TEMP_MEDIA.name

import server


class AuthTestCase(unittest.TestCase):
    def setUp(self):
        self.media_dir = TEMP_MEDIA.name
        with open(os.path.join(self.media_dir, "sample.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff\xe0fakejpeg")
        self.original_config = dict(server.CONFIG)
        self.original_media = server.MEDIA_DIR
        self.client = server.app.test_client()

    def tearDown(self):
        server.CONFIG.clear()
        server.CONFIG.update(self.original_config)
        server.MEDIA_DIR = self.original_media
        TEMP_MEDIA.cleanup()
        os.makedirs(self.media_dir, exist_ok=True)

    def set_pin(self, pin):
        server.CONFIG["auth_pin"] = pin

    def login(self, pin):
        return self.client.post("/api/auth/login", json={"pin": pin})


class NoPinConfigured(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.set_pin("")

    def test_status_is_open(self):
        self.assertEqual(self.client.get("/api/status").status_code, 200)

    def test_files_are_open(self):
        self.assertEqual(self.client.get("/api/files").status_code, 200)

    def test_media_is_open(self):
        res = self.client.get("/media/sample.jpg")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, b"\xff\xd8\xff\xe0fakejpeg")
        res.close()

    def test_shell_loads_without_auth(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn('id="authOverlay"', body)
        self.assertIn("submitUnlock", body)

    def test_login_reports_no_pin_required(self):
        res = self.login("anything")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.get_json()["pin_required"])


class PinConfigured(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.set_pin("4242")

    def test_status_requires_auth(self):
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 401)
        self.assertIn("PIN required", res.get_json()["error"])

    def test_status_with_token_header(self):
        res = self.client.get("/api/status", headers={"X-Auth-Token": "4242"})
        self.assertEqual(res.status_code, 200)

    def test_status_with_query_token(self):
        self.assertEqual(self.client.get("/api/status?token=4242").status_code, 200)

    def test_status_with_wrong_token(self):
        self.assertEqual(self.client.get("/api/status", headers={"X-Auth-Token": "0000"}).status_code, 401)

    def test_media_requires_auth(self):
        res = self.client.get("/media/sample.jpg")
        self.assertEqual(res.status_code, 401)

    def test_media_with_wrong_token(self):
        self.assertEqual(self.client.get("/media/sample.jpg?token=0000").status_code, 401)

    def test_login_rejects_wrong_pin(self):
        res = self.login("0000")
        self.assertEqual(res.status_code, 401)
        self.assertIn("Incorrect PIN", res.get_json()["error"])

    def test_login_sets_cookie_and_unlocks(self):
        res = self.login("4242")
        self.assertEqual(res.status_code, 200)
        self.assertIn("pimedia_auth=4242", res.headers["Set-Cookie"])
        self.assertEqual(self.client.get("/api/status").status_code, 200)

    def test_media_after_login(self):
        self.login("4242")
        res = self.client.get("/media/sample.jpg")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data, b"\xff\xd8\xff\xe0fakejpeg")
        res.close()

    def test_other_endpoints_require_auth(self):
        self.assertEqual(self.client.post("/api/playback/pause").status_code, 401)
        self.assertEqual(self.client.post("/api/delete", json={"filename": "sample.jpg"}).status_code, 401)
        self.assertEqual(self.client.get("/api/files").status_code, 401)

    def test_shell_still_loads_while_locked(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("authOverlay", res.get_data(as_text=True))


class PinLifecycle(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.set_pin("")
        self.saved = []
        self.original_save = server.save_config
        server.save_config = lambda cfg: self.saved.append(dict(cfg))

    def tearDown(self):
        server.save_config = self.original_save
        super().tearDown()

    def test_first_pin_can_be_set_while_open(self):
        res = self.client.post("/api/settings/pin", json={"pin": "1234"})
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["pin_configured"])
        self.assertEqual(server.CONFIG["auth_pin"], "1234")
        self.assertIn("pimedia_auth=1234", res.headers["Set-Cookie"])
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        res = self.client.get("/media/sample.jpg")
        self.assertEqual(res.status_code, 200)
        res.close()

    def test_session_survives_own_pin_change(self):
        self.client.post("/api/settings/pin", json={"pin": "1234"})
        res = self.client.post("/api/settings/pin", json={"pin": "5678"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        self.assertIn("pimedia_auth=5678", res.headers["Set-Cookie"])

    def test_clearing_pin_reopens_access(self):
        self.client.post("/api/settings/pin", json={"pin": "1234"})
        res = self.client.post("/api/settings/pin", json={"pin": ""})
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.get_json()["pin_configured"])
        self.assertEqual(server.CONFIG["auth_pin"], "")
        self.assertIn("pimedia_auth=;", res.headers["Set-Cookie"])
        self.assertEqual(self.client.get("/api/status").status_code, 200)
        res = self.client.get("/media/sample.jpg")
        self.assertEqual(res.status_code, 200)
        res.close()

    def test_locked_out_client_cannot_change_pin(self):
        self.client.post("/api/settings/pin", json={"pin": "1234"})
        locked = server.app.test_client()
        res = locked.post("/api/settings/pin", json={"pin": "0000"})
        self.assertEqual(res.status_code, 401)
        self.assertEqual(server.CONFIG["auth_pin"], "1234")
        self.assertEqual(locked.get("/api/status").status_code, 401)

    def test_login_does_not_persist_config(self):
        self.client.post("/api/settings/pin", json={"pin": "1234"})
        self.assertEqual(self.saved[-1]["auth_pin"], "1234")
        self.login("1234")
        self.assertEqual(self.saved[-1]["auth_pin"], "1234")
        self.assertEqual(len(self.saved), 1)


class UploadAuth(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.set_pin("4242")

    def test_upload_requires_auth(self):
        res = self.client.post(
            "/api/upload",
            data={"file": (io.BytesIO(b"data"), "clip.mp4")},
            content_type="multipart/form-data",
        )
        self.assertEqual(res.status_code, 401)
        self.assertFalse(os.path.exists(os.path.join(self.media_dir, "clip.mp4")))

    def test_upload_after_login(self):
        self.login("4242")
        res = self.client.post(
            "/api/upload",
            data={"file": (io.BytesIO(b"data"), "clip.mp4")},
            content_type="multipart/form-data",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(os.path.exists(os.path.join(self.media_dir, "clip.mp4")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
