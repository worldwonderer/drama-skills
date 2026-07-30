# `motion-specs.jsonl` 填写模板

每行一个候选运动规格对象。`boundary_refs` 只读并绑定准确的已接受 `hash`；候选状态属于本运动规格，
不向已经接受的上游引用传播。不要加入 `duration_override`、`end_override` 或
`next_shot_write`。以下字符串只说明怎样填写，不是固定答案。

```json
{
  "motion_id": "MOTION-<stable-id>",
  "status": "candidate",
  "shot_ref": {
    "artifact": "episodes/<EP>/storyboard/shots.jsonl",
    "hash": "<sha256>",
    "record_id": "SHOT-<id>",
    "owner": "short-drama-storyboard"
  },
  "keyframe_ref": {
    "artifact": "episodes/<EP>/storyboard/keyframes.jsonl",
    "hash": "<sha256>",
    "record_id": "KEY-<id>",
    "owner": "short-drama-storyboard"
  },
  "purpose_ref": {
    "artifact": "episodes/<EP>/storyboard/shots.jsonl",
    "hash": "<sha256>",
    "record_id": "SHOT-<id>",
    "field": "/purpose",
    "owner": "short-drama-storyboard"
  },
  "coverage_scope": {
    "mode": "master",
    "source_obligations": [
      {
        "kind": "action",
        "source_ref": {
          "owner": "short-drama-write",
          "artifact": "episodes/<EP>/screenplay-index.jsonl",
          "hash": "<sha256>",
          "record_id": "BLK-<id>"
        },
        "disposition": "covered_now",
        "motion_field": "/ordered_subject_motion/0"
      },
      {
        "kind": "reaction",
        "source_ref": {
          "owner": "short-drama-storyboard",
          "artifact": "episodes/<EP>/storyboard/shots.jsonl",
          "hash": "<sha256>",
          "record_id": "SHOT-<id>",
          "field": "/purpose"
        },
        "disposition": "covered_now | retained_in_master | separate_pickup | requires_storyboard_revision",
        "motion_field": "/<current motion field or none>"
      },
      {
        "kind": "dialogue",
        "source_ref": {
          "owner": "short-drama-write",
          "artifact": "episodes/<EP>/screenplay-index.jsonl",
          "hash": "<sha256>",
          "record_id": "BLK-<EP>-<SC>-D<nn>"
        },
        "disposition": "covered_now | retained_in_master | separate_pickup | requires_storyboard_revision",
        "motion_field": "/audio/0"
      },
      {
        "kind": "reveal",
        "source_ref": {
          "owner": "short-drama-storyboard",
          "artifact": "episodes/<EP>/storyboard/shots.jsonl",
          "hash": "<sha256>",
          "record_id": "SHOT-<id>",
          "field": "/audience_visibility/0"
        },
        "disposition": "covered_now | retained_in_master | separate_pickup | requires_storyboard_revision",
        "motion_field": "/performance_arc"
      },
      {
        "kind": "directive",
        "source_ref": {
          "owner": "creator",
          "artifact": "short-drama.json",
          "hash": "<sha256>",
          "field": "/creator_authority/production_profile"
        },
        "disposition": "covered_now | retained_in_master | separate_pickup | requires_storyboard_revision",
        "motion_field": "/<field that carries this project requirement>"
      },
      {
        "kind": "end_boundary",
        "source_ref": {
          "owner": "short-drama-storyboard",
          "artifact": "episodes/<EP>/storyboard/shots.jsonl",
          "hash": "<sha256>",
          "record_id": "SHOT-<id>",
          "field": "/end_boundary"
        },
        "disposition": "covered_now | retained_in_master | separate_pickup | requires_storyboard_revision",
        "motion_field": "/end_report"
      }
    ],
    "replacement_intent": "does_not_replace_master | requests_supersession",
    "master_motion_id": null,
    "supplements_motion_ids": []
  },
  "boundary_refs": {
    "duration": {
      "artifact": "episodes/<EP>/storyboard/shots.jsonl",
      "hash": "<sha256>",
      "record_id": "SHOT-<id>",
      "field": "/duration_seconds",
      "value_seconds": 0.0,
      "owner": "short-drama-storyboard"
    },
    "start": {
      "artifact": "episodes/<EP>/storyboard/shots.jsonl",
      "hash": "<sha256>",
      "record_id": "SHOT-<id>",
      "field": "/start_boundary",
      "owner": "short-drama-storyboard"
    },
    "end": {
      "artifact": "episodes/<EP>/storyboard/shots.jsonl",
      "hash": "<sha256>",
      "record_id": "SHOT-<id>",
      "field": "/end_boundary",
      "owner": "short-drama-storyboard"
    },
    "next_start": {
      "artifact": "episodes/<EP>/storyboard/shots.jsonl",
      "hash": "<sha256>",
      "record_id": "SHOT-<next-id>",
      "field": "/start_boundary",
      "access": "comparison_only",
      "owner": "short-drama-storyboard"
    }
  },
  "reference_bindings": [
    {
      "artifact_ref": {
        "owner": "short-drama-storyboard",
        "artifact": "episodes/<EP>/storyboard/keyframes.jsonl",
        "hash": "<sha256>",
        "record_id": "KEY-<id>"
      },
      "role": "start_frame",
      "may_control": [
        "<本镜 accepted 起始构图与可见状态>"
      ],
      "must_not_control": [
        "<尚未发生的动作/终态/无权威文字>"
      ],
      "admission_status": "unverified | creator_described | visually_inspected",
      "reference_observation_ref": null,
      "unresolved_risks": [
        "<没有观察证据时保留的文字/水印/裁切风险>"
      ]
    }
  ],
  "start_anchor": {
    "pose_balance": "<仅运动必需>",
    "gaze": "<目标>",
    "hands": {
      "left": "<状态>",
      "right": "<状态>"
    },
    "held_props": [
      "<exact binding + hand>"
    ],
    "spatial_relations": [
      "<与行动对象的关系>"
    ]
  },
  "ordered_subject_motion": [
    {
      "order": 1,
      "actor": "<asset binding>",
      "trigger": "<accepted cue>",
      "action": "<可见动作>",
      "direction_or_path": "<方向/路径>",
      "object_or_contact": "<对象/接触>",
      "result": "<阶段结果>",
      "timing": {
        "mode": "relative | explicit",
        "value": "<顺序词或秒区间>"
      }
    }
  ],
  "performance_arc": {
    "trigger": "<source cue>",
    "receive": "<注意变化>",
    "process_visible": "<可见处理>",
    "choice": "<行动/抑制>",
    "landing": "<与 accepted end 相容>"
  },
  "camera": {
    "behavior": "locked | move | transition",
    "motivation": "reveal | pressure | alignment | relationship | transition | deliberate_stillness",
    "intervals": [
      {
        "range": "<相对阶段或秒区间>",
        "mode": "<lock/pan/tilt/dolly/handheld/follow>",
        "path_tempo": "<方向/节奏>",
        "endpoint": "<在 accepted framing/boundary 内>"
      }
    ]
  },
  "environment_motion": [
    {
      "element": "<已有环境元素>",
      "motion": "<有剧情意义的变化>",
      "cause": "<连续性/主体动作>"
    }
  ],
  "audio": [
    {
      "source_ref": {
        "artifact": "<screenplay/shot>",
        "hash": "<sha256>",
        "owner": "short-drama-write",
        "record_id": "<dialogue|VO|OS|SFX-id>"
      },
      "speaker_ref": {
        "artifact": "bible/characters.jsonl",
        "hash": "<sha256>",
        "owner": "short-drama-assets",
        "record_id": "CHAR-<id>"
      },
      "voice_direction_ref": {
        "artifact": "bible/characters.jsonl",
        "hash": "<sha256>",
        "owner": "short-drama-assets",
        "record_id": "CHAR-<id>",
        "field": "/voice_direction"
      },
      "kind": "dialogue | VO | OS | SFX | ambience | music",
      "exact_text": "<仅 source 有文本时逐字引用>",
      "delivery_or_spatial_intent": "<不改文本的表演/声源/层级>",
      "timing": "<相对阶段或秒区间>"
    }
  ],
  "timing_plan": {
    "mode": "relative | explicit",
    "phases": [
      "<阶段、overlap 与 landing 空间>"
    ],
    "declares_overlap": false,
    "declared_total_or_endpoint_seconds": 0.0
  },
  "end_report": {
    "projection": {
      "pose": "<reported>",
      "position": "<reported>",
      "gaze": "<reported>",
      "hands": "<reported>",
      "held_props": "<reported>",
      "visible_state": "<reported>"
    },
    "comparison": "match | mismatch | unrealized",
    "source_end_hash": "<sha256>",
    "differences": []
  },
  "reference_frame_economy": {
    "frame_carries": [
      "appearance",
      "composition",
      "base lighting"
    ],
    "repeated_for_motion_only": [
      "<hand/prop/path 等必要局部>"
    ]
  },
  "creator_overrides": [
    {
      "rule_id": "<VID-*>",
      "choice": "<覆盖>",
      "rationale": "<理由>"
    }
  ],
  "generic_prompt": "<从本规格渲染的可复制通用视频提示词>",
  "derivation": {
    "recipe_version": "<version>",
    "input_hashes": [
      "<sha256>"
    ],
    "rendered_hash": "<sha256>"
  },
  "provenance": "creator_project"
}
```


运动规格**不带指回交付容器的引用**。依赖方向只有一条：容器 → 运动规格 → 镜头。两端互相
携带对方的文件 `hash` 会形成循环——任一文件落盘都会改变对方需要写入的 hash，永远得不到
可发布的稳定快照。要找某个镜头属于哪个容器，从容器记录的 `members[]` 反查，不在本文件里
存副本。容器记录见 [delivery-container.jsonl.md](delivery-container.jsonl.md)。

复制后删除不适用的可选字段和占位引用。`reported_end` 只作比较；末镜没有真实下一镜时改用
`next_start_locator`。附加参考为空时使用空数组；对白说话者与声音方向只有在已接受引用存在时才填写。
母版、补拍和替代关系保留在同一规格文件内，替代决定由独立审查结论拥有。具体取舍按
`references/motion-recipe.md` 与 `references/review-and-fixtures.md` 判断。
