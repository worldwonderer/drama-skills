import base64
import io
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock


SUITE = Path(__file__).resolve().parents[1]
SCRIPT = SUITE / "skills/short-drama-produce/scripts/provider_adapters.py"
SPEC = importlib.util.spec_from_file_location("short_drama_provider_adapters", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
provider_adapters = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provider_adapters)


class ProviderCompilerTests(unittest.TestCase):
    def reference_binding(self) -> dict[str, object]:
        return {
            "slot_id": "REF-HERO",
            "order": 1,
            "path": "输入/reference.png",
            "label": "女主定妆照",
            "role": "identity",
            "may_control": ["脸型", "发型"],
            "must_not_control": ["构图", "动作"],
        }

    def image_job(self, **parameters: object) -> dict[str, object]:
        return {
            "modality": "image",
            "prompt": "vertical character portrait",
            "references": [],
            "outputs": ["剧集/EP001/制作成果/images/portrait.png"],
            "parameters": parameters,
        }

    def video_job(self, **parameters: object) -> dict[str, object]:
        return {
            "modality": "video",
            "prompt": "slow push in as the evidence is revealed",
            "references": [],
            "outputs": ["剧集/EP001/制作成果/video/shot.mp4"],
            "parameters": parameters,
        }

    def music_job(self, **parameters: object) -> dict[str, object]:
        return {
            "modality": "music",
            "prompt": "restrained urban drama score",
            "references": [],
            "outputs": ["剧集/EP001/制作成果/music/cue.mp3"],
            "parameters": parameters,
        }

    def test_seedance_payload_is_explicit_and_contains_only_proven_fields(self) -> None:
        payload = provider_adapters.compile_seedance_payload(
            self.video_job(duration=5, ratio="9:16"),
            model="account-enabled-endpoint",
            allowed_ratios={"9:16"},
            duration_range=(5, 10),
        )
        self.assertEqual(
            payload,
            {
                "model": "account-enabled-endpoint",
                "content": [
                    {
                        "type": "text",
                        "text": "slow push in as the evidence is revealed",
                    }
                ],
                "ratio": "9:16",
                "duration": 5,
            },
        )

    def test_seedance_fails_closed_on_unproven_or_unsafe_inputs(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicitly configured"):
            provider_adapters.compile_seedance_payload(self.video_job(), model="")
        with self.assertRaisesRegex(ValueError, "supported profile"):
            provider_adapters.compile_seedance_payload(
                self.video_job(ratio="9:16 --seed 4"),
                model="configured",
                allowed_ratios={"9:16"},
            )
        with self.assertRaisesRegex(ValueError, "explicit model profile"):
            provider_adapters.compile_seedance_payload(
                self.video_job(duration=5), model="configured"
            )

        referenced = self.video_job()
        referenced["references"] = ["输入/reference.png"]
        with self.assertRaisesRegex(ValueError, "HTTPS or asset"):
            provider_adapters.compile_seedance_payload(
                referenced,
                model="configured",
                reference_urls=["data:image/png;base64,AAAA"],
                reference_roles=["reference_image"],
            )
        payload = provider_adapters.compile_seedance_payload(
            referenced,
            model="configured",
            reference_urls=["asset://asset-202608160001-example"],
            reference_roles=["reference_image"],
        )
        self.assertEqual(payload["content"][1]["role"], "reference_image")
        adaptive = provider_adapters.compile_seedance_payload(
            self.video_job(ratio="adaptive"),
            model="configured",
            allowed_ratios={"adaptive"},
        )
        self.assertEqual(adaptive["ratio"], "adaptive")

    def test_provider_prompts_preserve_confirmed_reference_semantics(self) -> None:
        video = self.video_job()
        video["references"] = ["输入/reference.png"]
        video["reference_bindings"] = [self.reference_binding()]
        seedance = provider_adapters.compile_seedance_payload(
            video,
            model="configured",
            reference_urls=["asset://asset-202608160001-example"],
            reference_roles=["reference_image"],
        )
        text = seedance["content"][0]["text"]
        self.assertIn("Reference @图片1 (女主定妆照), role identity", text)
        self.assertIn("May control: 脸型, 发型", text)
        self.assertIn("Must not control: 构图, 动作", text)

        image = self.image_job()
        image["references"] = ["输入/reference.png"]
        image["reference_bindings"] = [self.reference_binding()]
        gpt_image = provider_adapters.compile_gpt_image_2_payload(image)
        self.assertIn("Reference contract:", gpt_image["prompt"])
        self.assertIn("Must not control: 构图, 动作", gpt_image["prompt"])

    def test_minimax_continuation_keeps_both_media_and_uses_prompt_language(self) -> None:
        video = self.video_job(
            duration=6,
            resolution="768P",
            prompt_language="en",
        )
        video["references"] = ["输入/previous.mp4", "输入/previous-tail.png"]
        video["reference_bindings"] = [
            {
                **self.reference_binding(),
                "path": "输入/previous.mp4",
                "role": "continuity_video",
                "label": "上一段实际视频",
            },
            {
                **self.reference_binding(),
                "slot_id": "REF-TAIL",
                "order": 2,
                "path": "输入/previous-tail.png",
                "role": "actual_tail_frame",
                "label": "实际尾帧",
            },
        ]
        payload = provider_adapters.compile_minimax_h3_payload(
            video,
            model="configured",
            reference_urls=[
                "https://media.example/previous.mp4",
                "https://media.example/previous-tail.png",
            ],
            reference_roles=["reference_video", "reference_image"],
            allowed_resolutions={"768P"},
            duration_range=(6, 6),
        )
        text = payload["content"][0]["text"]
        self.assertIn("Reference contract:", text)
        self.assertIn("Reference <Video 1> (上一段实际视频), role continuity_video", text)
        self.assertIn("Reference <Picture 1> (实际尾帧), role actual_tail_frame", text)
        self.assertNotIn("prompt_language", payload)
        self.assertEqual(payload["content"][1]["type"], "video_url")
        self.assertEqual(payload["content"][2]["type"], "image_url")

    def test_seedance_2_5_task_types_compile_their_distinct_contracts(self) -> None:
        video = self.video_job(
            duration=20,
            ratio="9:16",
            generate_audio=True,
            omni_reference_task_type="reference",
            prompt_language="zh-CN",
        )
        video["references"] = ["输入/look.png"]
        video["reference_bindings"] = [
            {**self.reference_binding(), "path": "输入/look.png"}
        ]
        reference = provider_adapters.compile_seedance_payload(
            video,
            model="doubao-seedance-2-5-260628",
            reference_urls=["https://media.example/look.png"],
            reference_roles=["reference_image"],
            allowed_ratios={"9:16"},
            duration_range=(4, 30),
        )
        self.assertEqual(reference["duration"], 20)
        self.assertTrue(reference["generate_audio"])
        self.assertEqual(reference["omni_reference_task_type"], "reference")
        self.assertIn("参考 @图片1", reference["content"][0]["text"])

        edit = self.video_job(
            duration=-1,
            ratio="adaptive",
            omni_reference_task_type="edit",
        )
        edit["references"] = ["输入/source.mp4"]
        compiled_edit = provider_adapters.compile_seedance_payload(
            edit,
            model="doubao-seedance-2-5-260628",
            reference_urls=["https://media.example/source.mp4"],
            reference_roles=["reference_video"],
            allowed_ratios={"adaptive"},
        )
        self.assertEqual(compiled_edit["duration"], -1)
        with self.assertRaisesRegex(ValueError, "adaptive ratio and duration -1"):
            provider_adapters.compile_seedance_payload(
                {**edit, "parameters": {**edit["parameters"], "duration": 8}},
                model="doubao-seedance-2-5-260628",
                reference_urls=["https://media.example/source.mp4"],
                reference_roles=["reference_video"],
                allowed_ratios={"adaptive"},
                duration_range=(4, 30),
            )

        extend = self.video_job(
            duration=5,
            ratio="adaptive",
            omni_reference_task_type="extend",
            prompt_language="zh-CN",
        )
        extend["references"] = ["输入/previous.mp4", "输入/tail.png"]
        extend["reference_bindings"] = [
            {
                **self.reference_binding(),
                "path": "输入/previous.mp4",
                "label": "上一段实际视频",
                "role": "continuity_video",
            },
            {
                **self.reference_binding(),
                "slot_id": "REF-TAIL",
                "order": 2,
                "path": "输入/tail.png",
                "label": "实际尾帧",
                "role": "actual_tail_frame",
            },
        ]
        compiled_extend = provider_adapters.compile_seedance_payload(
            extend,
            model="doubao-seedance-2-5-260628",
            reference_urls=[
                "https://media.example/previous.mp4",
                "https://media.example/tail.png",
            ],
            reference_roles=["reference_video", "reference_image"],
            allowed_ratios={"adaptive"},
            duration_range=(4, 30),
        )
        extend_text = compiled_extend["content"][0]["text"]
        self.assertIn("输入素材 @视频1（上一段实际视频）", extend_text)
        self.assertIn("输入素材 @图片1（实际尾帧）", extend_text)
        self.assertNotIn("参考 @视频1", extend_text)
        self.assertEqual(compiled_extend["content"][1]["type"], "video_url")
        self.assertEqual(compiled_extend["content"][2]["type"], "image_url")

    def test_provider_rejects_reference_semantics_that_do_not_match_paths(self) -> None:
        image = self.image_job()
        image["references"] = ["输入/another.png"]
        image["reference_bindings"] = [self.reference_binding()]
        with self.assertRaisesRegex(ValueError, "path does not match"):
            provider_adapters.compile_gpt_image_2_payload(image)

    def test_gpt_image_2_payload_matches_the_current_contract(self) -> None:
        payload = provider_adapters.compile_gpt_image_2_payload(
            self.image_job(size="1152x2048", quality="medium")
        )
        self.assertEqual(
            payload,
            {
                "model": "gpt-image-2",
                "prompt": "vertical character portrait",
                "n": 1,
                "output_format": "png",
                "size": "1152x2048",
                "quality": "medium",
            },
        )

    def test_gpt_image_2_rejects_unsupported_and_invalid_geometry(self) -> None:
        for parameters in (
            {"background": "transparent"},
            {"input_fidelity": "high"},
            {"size": "1023x1024"},
            {"size": "512x512"},
            {"size": "3840x3840"},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                provider_adapters.compile_gpt_image_2_payload(
                    self.image_job(**parameters)
                )

    def test_minimax_music_payload_is_music_3_and_never_invents_duration(self) -> None:
        payload = provider_adapters.compile_minimax_music_payload(
            self.music_job(is_instrumental=True)
        )
        self.assertEqual(
            payload,
            {
                "model": "music-3.0",
                "prompt": "restrained urban drama score",
                "stream": False,
                "output_format": "hex",
                "audio_setting": {
                    "sample_rate": 44100,
                    "bitrate": 256000,
                    "format": "mp3",
                },
                "lyrics_optimizer": False,
                "is_instrumental": True,
            },
        )
        self.assertNotIn("duration", payload)

    def test_minimax_rejects_tts_missing_lyrics_and_instrumental_lyrics(self) -> None:
        tts = self.music_job(is_instrumental=True)
        tts["modality"] = "tts"
        with self.assertRaisesRegex(ValueError, "music job"):
            provider_adapters.compile_minimax_music_payload(tts)
        with self.assertRaisesRegex(ValueError, "requires confirmed lyrics"):
            provider_adapters.compile_minimax_music_payload(self.music_job())
        with self.assertRaisesRegex(ValueError, "must not carry lyrics"):
            provider_adapters.compile_minimax_music_payload(
                self.music_job(is_instrumental=True, lyrics="not allowed")
            )
        with self.assertRaisesRegex(ValueError, "optimizer is forbidden"):
            provider_adapters.compile_minimax_music_payload(
                self.music_job(lyrics_optimizer=True)
            )


    def minimax_video_profile(self) -> dict[str, object]:
        return {
            "allowed_ratios": {"9:16"},
            "allowed_resolutions": {"768P"},
            "duration_range": (4, 15),
        }

    def test_minimax_h3_payload_carries_only_configured_values(self) -> None:
        payload = provider_adapters.compile_minimax_h3_payload(
            self.video_job(duration=6, ratio="9:16", resolution="768P"),
            model="account-enabled-video-model",
            **self.minimax_video_profile(),
        )
        self.assertEqual(
            payload,
            {
                "model": "account-enabled-video-model",
                "content": [
                    {
                        "type": "text",
                        "text": "slow push in as the evidence is revealed",
                    }
                ],
                "duration": 6,
                "resolution": "768P",
                "ratio": "9:16",
            },
        )

    def test_minimax_h3_requires_an_explicit_runtime_profile(self) -> None:
        job = self.video_job(duration=6, ratio="9:16", resolution="768P")
        with self.assertRaisesRegex(ValueError, "explicitly configured"):
            provider_adapters.compile_minimax_h3_payload(
                job, model="", **self.minimax_video_profile()
            )
        with self.assertRaisesRegex(ValueError, "duration needs an explicit model profile"):
            provider_adapters.compile_minimax_h3_payload(
                job,
                model="m",
                allowed_ratios={"9:16"},
                allowed_resolutions={"768P"},
            )
        with self.assertRaisesRegex(ValueError, "resolution needs an explicit model profile"):
            provider_adapters.compile_minimax_h3_payload(
                job, model="m", allowed_ratios={"9:16"}, duration_range=(4, 15)
            )
        with self.assertRaisesRegex(ValueError, "ratio needs an explicit model profile"):
            provider_adapters.compile_minimax_h3_payload(
                job,
                model="m",
                allowed_resolutions={"768P"},
                duration_range=(4, 15),
            )
        with self.assertRaisesRegex(ValueError, "outside the configured model profile"):
            provider_adapters.compile_minimax_h3_payload(
                self.video_job(duration=30, ratio="9:16", resolution="768P"),
                model="m",
                **self.minimax_video_profile(),
            )
        with self.assertRaisesRegex(ValueError, "outside the supported profile"):
            provider_adapters.compile_minimax_h3_payload(
                self.video_job(duration=6, ratio="9:16", resolution="8K"),
                model="m",
                **self.minimax_video_profile(),
            )

    def test_minimax_h3_text_to_video_needs_a_concrete_ratio(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires an explicit ratio"):
            provider_adapters.compile_minimax_h3_payload(
                self.video_job(duration=6, resolution="768P"),
                model="m",
                **self.minimax_video_profile(),
            )
        with self.assertRaisesRegex(ValueError, "adaptive ratio"):
            provider_adapters.compile_minimax_h3_payload(
                self.video_job(duration=6, ratio="adaptive", resolution="768P"),
                model="m",
                allowed_ratios={"adaptive"},
                allowed_resolutions={"768P"},
                duration_range=(4, 15),
            )

    def test_minimax_h3_references_need_a_hosted_uri_and_a_declared_role(self) -> None:
        job = {
            **self.video_job(duration=6, resolution="768P"),
            "references": ["输入/first.png"],
            "reference_bindings": [
                {
                    **self.reference_binding(),
                    "path": "输入/first.png",
                    "role": "start_frame",
                }
            ],
        }
        payload = provider_adapters.compile_minimax_h3_payload(
            job,
            model="m",
            reference_urls=["https://cdn.example/first.png"],
            reference_roles=["first_frame"],
            **self.minimax_video_profile(),
        )
        self.assertEqual(
            payload["content"][1],
            {
                "type": "image_url",
                "image_url": {"url": "https://cdn.example/first.png"},
                "role": "first_frame",
            },
        )
        self.assertNotIn("ratio", payload)
        with self.assertRaisesRegex(ValueError, "HTTPS or mm_file"):
            provider_adapters.compile_minimax_h3_payload(
                job,
                model="m",
                reference_urls=["file:///etc/passwd"],
                reference_roles=["first_frame"],
                **self.minimax_video_profile(),
            )
        with self.assertRaisesRegex(ValueError, "unsupported MiniMax reference role"):
            provider_adapters.compile_minimax_h3_payload(
                job,
                model="m",
                reference_urls=["https://cdn.example/first.png"],
                reference_roles=["identity"],
                **self.minimax_video_profile(),
            )

    def test_minimax_h3_rejects_mixed_frame_and_reference_modes(self) -> None:
        job = {
            **self.video_job(duration=6, resolution="768P"),
            "references": ["输入/previous.mp4", "输入/tail.png"],
        }
        with self.assertRaisesRegex(ValueError, "cannot be mixed"):
            provider_adapters.compile_minimax_h3_payload(
                job,
                model="m",
                reference_urls=[
                    "https://media.example/previous.mp4",
                    "https://media.example/tail.png",
                ],
                reference_roles=["reference_video", "first_frame"],
                **self.minimax_video_profile(),
            )
    def test_minimax_h3_refuses_a_prompt_beyond_the_provider_limit(self) -> None:
        long_job = {
            **self.video_job(duration=6, ratio="9:16", resolution="768P"),
            "prompt": "a" * (provider_adapters.MINIMAX_VIDEO_PROMPT_LIMIT + 1),
        }
        with self.assertRaisesRegex(ValueError, "exceeds the provider limit"):
            provider_adapters.compile_minimax_h3_payload(
                long_job, model="m", **self.minimax_video_profile()
            )


class ProviderRuntimeTests(unittest.TestCase):
    def test_http_failures_expose_only_stable_whitelisted_evidence(self) -> None:
        cases = (
            (401, "authentication", False),
            (429, "rate_limit", True),
            (503, "server", True),
        )
        for status, category, retryable in cases:
            with self.subTest(status=status):
                error = urllib.error.HTTPError(
                    "https://api.openai.com/v1/images/generations",
                    status,
                    "secret provider message",
                    {
                        "x-request-id": f"req_{status}",
                        "authorization": "Bearer credential-must-not-appear",
                    },
                    io.BytesIO(
                        json.dumps(
                            {
                                "error": {
                                    "code": "rate_limit_exceeded",
                                    "message": "credential-must-not-appear",
                                }
                            }
                        ).encode()
                    ),
                )
                public = provider_adapters._http_failure(
                    "gpt-image-2", error
                ).public("gpt-image-2")
                error.close()
                self.assertEqual(public["http_status"], status)
                self.assertEqual(public["category"], category)
                self.assertEqual(public["retryable"], retryable)
                self.assertEqual(public["code"], "rate_limit_exceeded")
                self.assertEqual(public["request_id"], f"req_{status}")
                self.assertNotIn("message", public)
                self.assertNotIn("credential", json.dumps(public))

    def test_each_runtime_maps_a_successful_offline_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            common_env = {
                "ARK_API_KEY": "ark-secret",
                "SEEDANCE_MODEL": "configured-endpoint",
                "OPENAI_API_KEY": "openai-secret",
                "MINIMAX_API_KEY": "minimax-secret",
            }
            with mock.patch.dict(os.environ, common_env, clear=False):
                with mock.patch.object(
                    provider_adapters,
                    "_request_json",
                    side_effect=[
                        ({"id": "seedance-task"}, {}),
                        (
                            {
                                "status": "succeeded",
                                "content": {"video_url": "https://cdn.example/shot.mp4"},
                            },
                            {},
                        ),
                    ],
                ), mock.patch.object(
                    provider_adapters,
                    "_download",
                    return_value=root / "shot.mp4",
                ):
                    path, task_id = provider_adapters._run_seedance(
                        {
                            "modality": "video",
                            "prompt": "camera holds",
                            "references": [],
                            "outputs": ["制作成果/shot.mp4"],
                            "parameters": {},
                            "project_root": str(root),
                            "output_root": str(root),
                        }
                    )
                    self.assertEqual((path, task_id), (root / "shot.mp4", "seedance-task"))

                with mock.patch.object(
                    provider_adapters,
                    "_request_json",
                    return_value=(
                        {
                            "data": [
                                {
                                    "b64_json": base64.b64encode(
                                        b"\x89PNG\r\n\x1a\ncontent"
                                    ).decode()
                                }
                            ]
                        },
                        {"x-request-id": "openai-request"},
                    ),
                ):
                    path, request_id = provider_adapters._run_openai(
                        {
                            "modality": "image",
                            "prompt": "portrait",
                            "references": [],
                            "outputs": ["制作成果/portrait.png"],
                            "parameters": {},
                            "project_root": str(root),
                            "output_root": str(root),
                        }
                    )
                    self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))
                    self.assertEqual(request_id, "openai-request")

                with mock.patch.object(
                    provider_adapters,
                    "_request_json",
                    return_value=(
                        {
                            "data": {"audio": b"ID3music".hex(), "status": 2},
                            "trace_id": "minimax-trace",
                            "base_resp": {"status_code": 0},
                        },
                        {},
                    ),
                ):
                    path, trace_id = provider_adapters._run_minimax(
                        {
                            "modality": "music",
                            "prompt": "score",
                            "references": [],
                            "outputs": ["制作成果/cue.mp3"],
                            "parameters": {"is_instrumental": True},
                            "project_root": str(root),
                            "output_root": str(root),
                        }
                    )
                    self.assertEqual(path.read_bytes(), b"ID3music")
                    self.assertEqual(trace_id, "minimax-trace")

    def test_minimax_video_runtime_maps_a_successful_offline_response(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = {
                "MINIMAX_API_KEY": "minimax-secret",
                "MINIMAX_VIDEO_MODEL": "configured-video-model",
                "MINIMAX_VIDEO_RESOLUTIONS": "768P",
                "MINIMAX_VIDEO_RATIOS": "9:16",
                "MINIMAX_VIDEO_MIN_DURATION": "4",
                "MINIMAX_VIDEO_MAX_DURATION": "15",
            }
            job = {
                "modality": "video",
                "prompt": "camera holds",
                "references": [],
                "outputs": ["制作成果/shot.mp4"],
                "parameters": {"duration": 6, "ratio": "9:16", "resolution": "768P"},
                "project_root": str(root),
                "output_root": str(root),
            }
            with mock.patch.dict(os.environ, environment, clear=False):
                with mock.patch.object(
                    provider_adapters,
                    "_request_json",
                    side_effect=[
                        ({"task_id": "minimax-video-task"}, {}),
                        ({"task": {"status": "running"}}, {}),
                        (
                            {
                                "task": {
                                    "status": "succeeded",
                                    "content": {"url": "https://cdn.example/shot.mp4"},
                                }
                            },
                            {},
                        ),
                    ],
                ), mock.patch.object(
                    provider_adapters, "_download", return_value=root / "shot.mp4"
                ), mock.patch.object(provider_adapters.time, "sleep"):
                    path, task_id = provider_adapters._run_minimax_video(job)
                self.assertEqual((path, task_id), (root / "shot.mp4", "minimax-video-task"))

                with mock.patch.object(
                    provider_adapters,
                    "_request_json",
                    side_effect=[
                        ({"task_id": "minimax-video-task"}, {}),
                        ({"task": {"status": "surprising"}}, {}),
                    ],
                ), mock.patch.object(provider_adapters.time, "sleep"):
                    with self.assertRaises(provider_adapters.AdapterFailure) as raised:
                        provider_adapters._run_minimax_video(job)
                self.assertEqual(
                    raised.exception.public("minimax-h3")["code"], "unknown_task_status"
                )

    def test_minimax_video_runtime_refuses_an_invalid_profile(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = {
                "modality": "video",
                "prompt": "camera holds",
                "references": [],
                "outputs": ["制作成果/shot.mp4"],
                "parameters": {"duration": 6, "ratio": "9:16", "resolution": "768P"},
                "project_root": str(root),
                "output_root": str(root),
            }
            broken = {
                "MINIMAX_API_KEY": "minimax-secret",
                "MINIMAX_VIDEO_MODEL": "configured-video-model",
                "MINIMAX_VIDEO_RESOLUTIONS": "4K",
                "MINIMAX_VIDEO_MIN_DURATION": "4",
                "MINIMAX_VIDEO_MAX_DURATION": "15",
            }
            with mock.patch.dict(os.environ, broken, clear=False):
                with self.assertRaises(provider_adapters.AdapterFailure) as raised:
                    provider_adapters._run_minimax_video(job)
            self.assertEqual(
                raised.exception.public("minimax-h3")["code"], "invalid_model_profile"
            )

    def test_seedance_runtime_refuses_unhosted_local_references(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "reference.png").write_bytes(b"PNG")
            with mock.patch.dict(
                os.environ,
                {"ARK_API_KEY": "secret", "SEEDANCE_MODEL": "configured"},
                clear=False,
            ):
                with self.assertRaisesRegex(
                    provider_adapters.AdapterFailure, "trusted HTTPS upload"
                ):
                    provider_adapters._run_seedance(
                        {
                            "modality": "video",
                            "prompt": "camera holds",
                            "references": ["reference.png"],
                            "outputs": ["制作成果/shot.mp4"],
                            "parameters": {},
                            "project_root": str(root),
                        }
                    )

    def test_provider_output_must_match_the_confirmed_media_type(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(
                provider_adapters.AdapterFailure, "target media type"
            ):
                provider_adapters._temporary_output(
                    {"output_root": directory},
                    "制作成果/frame.png",
                    b"not-a-png",
                )

    def test_multipart_references_are_bounded_and_read_from_pinned_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.png"
            path.write_bytes(b"small-image")
            encoded, content_type = provider_adapters._multipart(
                {"model": "gpt-image-2"}, [path]
            )
            self.assertIn(b"small-image", encoded)
            self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
            with mock.patch.object(provider_adapters, "MAX_REFERENCE_BYTES", 4):
                with self.assertRaisesRegex(ValueError, "size limit"):
                    provider_adapters._multipart({}, [path])

    def test_reference_bytes_survive_a_text_mode_descriptor(self) -> None:
        # 回归：_read_reference 曾用 os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        # 打开描述符。Windows 没有 O_BINARY 时那是文本模式的描述符——os.read 会
        # 把每个 \r\n 折成 \n，并在第一个 0x1A（DOS 文件结束符）处停住。PNG 签名
        # 是 89 50 4E 47 0D 0A 1A 0A，第七个字节正是 0x1A，于是每张参考图都以
        # 5 字节送到 gpt-image-2，供应商回 invalid_image_file / HTTP 400，
        # references 与 reference_bindings 在这个平台上从未成功过。
        #
        # POSIX 没有文本模式，直接断言读回的字节数无法在这里变红。所以本例把
        # O_BINARY 立成一个真实常量，并让 os.read 在调用方没有点名二进制时按
        # Windows CRT 的方式截断：断言的是这段代码有没有「要求」二进制模式。
        payload = b"\x89PNG\r\n\x1a\nIHDR\r\ntail"
        native_binary = getattr(os, "O_BINARY", 0)
        probe = native_binary or 0x8000  # MSVC CRT 的 _O_BINARY
        real_open, real_read = os.open, os.read
        text_mode: set[int] = set()

        def fake_open(target, flags, *args, **kwargs):  # type: ignore[no-untyped-def]
            descriptor = real_open(
                target, (flags & ~probe) | native_binary, *args, **kwargs
            )
            if not flags & probe:
                text_mode.add(descriptor)
            return descriptor

        def fake_read(descriptor: int, length: int) -> bytes:
            data = real_read(descriptor, length)
            if descriptor in text_mode:
                return data.replace(b"\r\n", b"\n").split(b"\x1a", 1)[0]
            return data

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.png"
            path.write_bytes(payload)
            with mock.patch.object(os, "O_BINARY", probe, create=True), mock.patch.object(
                os, "open", fake_open
            ), mock.patch.object(os, "read", fake_read):
                content = provider_adapters._read_reference(path)
        self.assertEqual(content, payload)

    def test_cli_selftest_and_safe_failure_do_not_expose_credentials(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--selftest"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

        secret = "credential-must-not-appear"
        env = os.environ.copy()
        env["OPENAI_API_KEY"] = secret
        failed = subprocess.run(
            [sys.executable, str(SCRIPT), "gpt-image-2"],
            input=json.dumps({"modality": "video"}),
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(failed.returncode, 1)
        public = json.loads(failed.stdout)
        self.assertEqual(
            public,
            {
                "error": {
                    "provider": "gpt-image-2",
                    "category": "invalid_request",
                    "code": "invalid_job",
                    "retryable": False,
                }
            },
        )
        self.assertEqual(failed.stderr.strip(), "provider adapter failed safely")
        self.assertNotIn(secret, failed.stdout)
        self.assertNotIn(secret, failed.stderr)


if __name__ == "__main__":
    unittest.main()
