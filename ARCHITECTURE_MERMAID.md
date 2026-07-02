# nemohermes_bks — Architecture Diagrams

Рендерится нативно в GitLab / GitHub · [mermaid.live](https://mermaid.live) для редактирования

**Цветовой код (единый для всех диаграмм):**

| Цвет | Компонент |
|---|---|
| 🟤 Тёмно-серый | Developer / внешние участники |
| 🟠 Оранжевый | GitLab CI / Pipeline stages |
| 🟢 Зелёный | Агенты Hermes |
| 🔵 Синий | Роутер / K3s Deployments / Services |
| 🟣 Фиолетовый | GPU / vLLM / Security |
| 🩵 Голубой | Cloud LLM APIs |
| 🟡 Янтарный | Хранилище (PVC, Storage, Secret) |
| 🔴 Красный | Gate / Блокировка / Registry |

---

## 1 · System Overview

```mermaid
flowchart LR
    classDef dev      fill:#1e293b,stroke:#475569,color:#f8fafc,stroke-width:2px
    classDef ci       fill:#c2410c,stroke:#ea580c,color:#fff,stroke-width:2px
    classDef registry fill:#7f1d1d,stroke:#dc2626,color:#fff,stroke-width:2px
    classDef agent    fill:#15803d,stroke:#22c55e,color:#fff,stroke-width:2px
    classDef router   fill:#1d4ed8,stroke:#60a5fa,color:#fff,stroke-width:2px
    classDef vllm     fill:#6d28d9,stroke:#a78bfa,color:#fff,stroke-width:2px
    classDef cloud    fill:#0369a1,stroke:#38bdf8,color:#fff,stroke-width:2px
    classDef memory   fill:#92400e,stroke:#fbbf24,color:#fff,stroke-width:2px
    classDef external fill:#374151,stroke:#6b7280,color:#fff,stroke-width:2px

    DEV(["👨‍💻 Developer"]):::dev
    TG(["✈️ Telegram"]):::external

    subgraph GH["GitHub · rndkrkn-boop"]
        GHBKS["bksamotsvety\nsource-of-truth"]:::dev
    end

    subgraph GITLAB["GitLab CE · :8929"]
        GL["CI Pipeline\nrouter · memgraphrag\nsandbox-templates"]:::ci
        REG[("Registry :5050")]:::registry
        GLBKS["bks/bksamotsvety\npull mirror · CI/CD Vars"]:::ci
        GL -->|kaniko| REG
        GHBKS -.->|"pull mirror\nPAT"| GLBKS
    end

    subgraph SAND["OpenShell Sandbox  bks-production"]
        AG["8 Hermes Agents"]:::agent
    end

    subgraph DC["Docker Compose · host:4000 (production router)"]
        DCR["router\nclassifier:4000 + litellm:4001\nSECURITY_MODEL=security-lora-v1"]:::router
    end

    subgraph GB10["GPU Hardware · GB10 Grace-Blackwell\nvllm-classifier (не K8s)"]
        VLLM["vllm-classifier\nQwen3.5-0.8B · GPU\nsecurity-lora-v1 · pii-cleaner-lora-v1"]:::vllm
    end

    subgraph K3S["K3s Cluster · 192.168.2.180
manifests: router/deploy/ +
MemGraphRAG/deploy/"]
        subgraph NSM["memgraphrag"]
            MGR["MemGraphRAG :8000"]:::memory
        end
    end

    subgraph APIS["Cloud LLM APIs
(openai/nvidia/... · anthropic/...]:::cloud]
        NV["NVIDIA\ncheap · mid · large"]:::cloud
        AC["Anthropic\nlarge fallback"]:::cloud
    end

    DEV -->|"git push\nbksamotsvety"| GHBKS
    DEV -->|"git push\nrouter · mgr · templates"| GL
    REG -.->|pull on restart| K3S

    TG --> AG
    AG -->|/v1/chat/completions| DCR
    DCR -->|"http://vllm-classifier:8000"| VLLM
    DCR -->|proxy| NV & AC
    AG -->|episodes · retrieve| MGR

    style GH     fill:none,stroke:#475569,stroke-width:2px
    style GITLAB  fill:none,stroke:#c2410c,stroke-width:2px
    style SAND    fill:none,stroke:#15803d,stroke-width:2px
    style DC      fill:none,stroke:#1d4ed8,stroke-width:2px
    style GB10    fill:none,stroke:#6d28d9,stroke-width:2px
    style K3S     fill:none,stroke:#374151,stroke-width:2px
    style APIS    fill:none,stroke:#0369a1,stroke-width:2px
```

---

## 2 · CI/CD Pipeline

```mermaid
flowchart TD
    classDef dev    fill:#1e293b,stroke:#475569,color:#f8fafc,stroke-width:2px
    classDef stage  fill:#1e3a8a,stroke:#3b82f6,color:#fff,stroke-width:1px
    classDef ok     fill:#166534,stroke:#22c55e,color:#fff,stroke-width:2px
    classDef warn   fill:#92400e,stroke:#f59e0b,color:#fff,stroke-width:1px
    classDef fail   fill:#7f1d1d,stroke:#ef4444,color:#fff,stroke-width:2px
    classDef ci     fill:#c2410c,stroke:#f97316,color:#fff,stroke-width:2px
    classDef runner fill:#374151,stroke:#9ca3af,color:#fff,stroke-width:1px
    classDef reg    fill:#7f1d1d,stroke:#dc2626,color:#fff,stroke-width:2px

    PUSH(["git push\nGitLab · router/mgr/templates"]):::dev
    PUSHGH(["git push\nGitHub · bksamotsvety"]):::dev

    subgraph HOOK["Pre-push Hook  ·  router only
.githooks/pre-push
5 файлов, style: пропуск"]
        H1{"файлы роутера\nизменились?"}:::stage
        H2{"все коммиты\nstyle: ?"}:::stage
        H3["eval/gate.py\nclaude-eval sandbox"]:::fail
        HW["⚠ INFRA-WARN\nсессия протухла\npush разрешён"]:::warn
        HO["✓ GATE: OK"]:::ok
        HF["✗ GATE: FAIL\npush заблокирован"]:::fail
        H1 -->|нет| HO
        H1 -->|да| H2
        H2 -->|да| HO
        H2 -->|нет| H3
        H3 -->|"claude-cli\nнедоступен"| HW
        H3 -->|"регрессия ≥ 1.5"| HF
        H3 -->|"в норме"| HO
    end

    PUSH --> H1
    HO & HW --> GL
    HF -->|"git push\n--no-verify"| GL

    subgraph GH["GitHub"]
        GHB["rndkrkn-boop/bksamotsvety"]:::runner
    end
    PUSHGH --> GHB

    subgraph GL["GitLab CE"]
        subgraph PB["bks/bksamotsvety"]
            BM["pull mirror\nCI/CD Variables: 35"]:::runner
        end
        GHB -.->|"PAT pull mirror"| BM

        subgraph PR["bks/router
CI: .gitlab-ci.yml · 4 stage"]
            R1["lint
ruff check + format"]:::ci
            R2["eval-config
render + smoke"]:::ci
            R3["test → unit-test
25 тестов, junit report"]:::ok
            R4["kaniko build
--insecure
main→latest
dev→dev"]:::ci
            R1 --> R2 --> R3 --> R4
        end
        subgraph PM["bks/memgraphrag
lint·test·build (3 stage)"]
            M1["lint
ruff check + format"]:::ci
            M2["test
pytest tests/
54 total (3 files)"]:::ok
            M3["kaniko build
--timeout 30m
HF cache: build-arg"]:::ci
            M1 --> M2 --> M3
        end
        subgraph PS["bks/sandbox-templates
lint·validate"]
            S1["lint
ruff check"]:::ok
            S2["validate-presets
YAML parse + required keys
(host·port·protocol·enforcement)"]:::ok
            S1 --> S2
        end
    end  

    subgraph RUN["Runners"]
        RC["bks-docker-runner\ndocker · CPU"]:::runner
        RG["bks-gpu-runner\ndocker + nvidia CDI\ntags: gpu"]:::runner
    end
    GL -.->|executes jobs| RUN

    REG[("Registry\n192.168.2.180:5050")]:::reg
    R4 & M3 --> REG

    style HOOK fill:none,stroke:#7f1d1d,stroke-width:2px
    style GH   fill:none,stroke:#475569,stroke-width:2px
    style GL   fill:none,stroke:#c2410c,stroke-width:2px
    style PB   fill:none,stroke:#6b7280,stroke-width:1px,stroke-dasharray:4
    style PR   fill:none,stroke:#f97316,stroke-width:1px,stroke-dasharray:4
    style PM   fill:none,stroke:#f97316,stroke-width:1px,stroke-dasharray:4
    style PS   fill:none,stroke:#f97316,stroke-width:1px,stroke-dasharray:4
    style RUN  fill:none,stroke:#374151,stroke-width:2px
```

---

## 3 · Agent Runtime

```mermaid
flowchart LR
    classDef tg      fill:#1d4ed8,stroke:#60a5fa,color:#fff,stroke-width:2px
    classDef auto    fill:#15803d,stroke:#22c55e,color:#fff,stroke-width:2px
    classDef mid     fill:#0f766e,stroke:#2dd4bf,color:#fff,stroke-width:2px
    classDef large   fill:#1e3a8a,stroke:#93c5fd,color:#fff,stroke-width:2px
    classDef router  fill:#1d4ed8,stroke:#60a5fa,color:#fff,stroke-width:2px
    classDef vllm    fill:#6d28d9,stroke:#a78bfa,color:#fff,stroke-width:2px
    classDef cloud   fill:#0369a1,stroke:#38bdf8,color:#fff,stroke-width:2px
    classDef memory  fill:#92400e,stroke:#fbbf24,color:#fff,stroke-width:2px
    classDef ext     fill:#374151,stroke:#6b7280,color:#fff,stroke-width:1px

    TGP(["✈️ Telegram\nпрод-группа"]):::tg
    TGM(["✈️ Telegram\nmkt-группа"]):::tg

    subgraph SAND["OpenShell Sandbox  bks-production  ·  SSRF-guard"]
        subgraph GA["auto tier"]
            DB["director-bot"]:::auto
            EXP["experiment"]:::auto
            MKT["mkt-bot"]:::auto
        end
        subgraph GM["mid tier"]
            MM["market-monitor"]:::mid
            ST["structuring"]:::mid
        end
        subgraph GL["large tier"]
            AN["analytics"]:::large
            RS["research"]:::large
            CN["content"]:::large
        end
    end

    TGP --> DB & EXP
    TGM --> MKT & MM
    AN & MM -.->|дайджест| TGM

    subgraph ROUTER["Docker Compose Router · :4000"]
        CLS["classifier.py\n:4000"]:::router
        VLLM_K["vllm-classifier\nQwen3.5-0.8B · GPU\n:8000\nsecurity-lora-v1\npii-cleaner-lora-v1"]:::vllm
        LL["LiteLLM proxy\n:4001"]:::router
        CLS -->|"fast-path / LLM-classify\n+ security check"| VLLM_K
        VLLM_K -->|"tier + risk"| CLS
        CLS -->|proxy| LL
    end

    DB & EXP & MKT & MM & AN & ST & RS & CN --> CLS

    subgraph CLOUD["Cloud LLM APIs"]
        NV["NVIDIA API\ncheap → nemotron-nano-30b\nmid  → nemotron-super-49b\nlarge → nemotron-ultra-550b"]:::cloud
        AC["Anthropic API\nclaude-sonnet-4-6\nlarge fallback"]:::cloud
    end
    LL --> NV & AC

    MGR["MemGraphRAG\n/api/episodes\n/api/retrieve"]:::memory
    WEB(["Internet\nnous-web"]):::ext
    STT["STT :10301\nfaster-whisper\nlarge-v3"]:::ext

    RS -->|code_execution| MGR
    RS & MM -->|web_search| WEB
    DB & MKT -.->|голос| STT

    style SAND   fill:none,stroke:#15803d,stroke-width:2px
    style GA     fill:none,stroke:#22c55e,stroke-width:1px,stroke-dasharray:4
    style GM     fill:none,stroke:#2dd4bf,stroke-width:1px,stroke-dasharray:4
    style GL     fill:none,stroke:#93c5fd,stroke-width:1px,stroke-dasharray:4
    style ROUTER fill:none,stroke:#1d4ed8,stroke-width:2px
    style CLOUD  fill:none,stroke:#0369a1,stroke-width:2px
```

---

## 4 · K3s Infrastructure

```mermaid
flowchart TD
    classDef reg    fill:#7f1d1d,stroke:#dc2626,color:#fff,stroke-width:2px
    classDef deploy fill:#1d4ed8,stroke:#60a5fa,color:#fff,stroke-width:2px
    classDef vllm   fill:#6d28d9,stroke:#a78bfa,color:#fff,stroke-width:2px
    classDef svc    fill:#1e40af,stroke:#93c5fd,color:#fff,stroke-width:1px
    classDef pvc    fill:#78350f,stroke:#f59e0b,color:#fff,stroke-width:1px
    classDef secret fill:#7f1d1d,stroke:#f87171,color:#fff,stroke-width:1px
    classDef cm     fill:#374151,stroke:#9ca3af,color:#fff,stroke-width:1px
    classDef plugin fill:#5b21b6,stroke:#c4b5fd,color:#fff,stroke-width:2px
    classDef agent  fill:#15803d,stroke:#22c55e,color:#fff,stroke-width:2px
    classDef disk   fill:#374151,stroke:#6b7280,color:#fff,stroke-width:1px

    REG[("Registry\n192.168.2.180:5050")]:::reg
    DISK[("hostPath\n/home/admin/models\nQwen3.5-0.8B\n+ adapters/")]:::disk

    subgraph K3S["K3s · 192.168.2.180  ✅ Running"]
        NVP["nvidia-device-plugin\nDaemonSet · kube-system\n⚠ GB10: nvmlGetMemoryInfo\nNot Supported — bypassed"]:::plugin

        subgraph NSR["namespace: bks-router\n(router DELETED 2026-07-02 ✓)"]
            VD["Deployment\nvllm-classifier ✅\nvllm-openai:nightly\nprivileged + /dev/nvidia*\nsecurity-lora-v1\npii-cleaner-lora-v1"]:::vllm
            SV["Service\nClusterIP :8000"]:::svc
            VD --> SV
        end

        subgraph NSM["namespace: memgraphrag"]
            SQ["Secret\nqdrant-apikey"]:::secret
            SM["Secret\nmemgraphrag-apikey"]:::secret
            PQ[("PVC qdrant-data\n20 Gi")]:::pvc
            PM[("PVC memgraphrag-data\n10 Gi")]:::pvc

            QD["Deployment\nqdrant\nqdrant:v1.9.7"]:::deploy
            SQS["Service\nClusterIP :6333"]:::svc

            MD["Deployment\nmemgraphrag\nbks/memgraphrag:latest\nContriever · igraph · SQLite"]:::deploy
            SMS["Service\nClusterIP :8000"]:::svc

            SQ & PQ --> QD
            QD --> SQS
            SQS -->|"svc:6333"| MD
            SM & PM --> MD
            MD --> SMS
        end

        NVP -.->|"bypassed (GB10)"| VD
    end

    REG -->|imagePullPolicy: Always| VD & MD
    DISK -->|hostPath mount| VD

    subgraph DCR["Docker Compose · host:4000 (production router)"]
        RD["router\nclassifier:4000 + litellm:4001"]:::deploy
    end

    AG(["Agents\nOpenShell sandbox"]):::agent
    AG -->|:4000| RD
    AG -->|"svc:8000"| SMS

    style K3S fill:none,stroke:#1d4ed8,stroke-width:3px
    style NSR fill:none,stroke:#3b82f6,stroke-width:2px,stroke-dasharray:5
    style NSM fill:none,stroke:#f59e0b,stroke-width:2px,stroke-dasharray:5
    style DCR fill:none,stroke:#1d4ed8,stroke-width:2px
```

---

## 5 · Security Layers

```mermaid
flowchart TD
    classDef tier    fill:#4c1d95,stroke:#a78bfa,color:#fff,stroke-width:2px
    classDef profile fill:#1e3a8a,stroke:#93c5fd,color:#fff,stroke-width:2px
    classDef preset  fill:#1e40af,stroke:#60a5fa,color:#fff,stroke-width:1px
    classDef guard   fill:#7f1d1d,stroke:#f87171,color:#fff,stroke-width:2px
    classDef rule    fill:#78350f,stroke:#fbbf24,color:#fff,stroke-width:1px
    classDef cred    fill:#166534,stroke:#4ade80,color:#fff,stroke-width:2px
    classDef note    fill:#1e293b,stroke:#94a3b8,color:#fbbf24,stroke-width:1px

    TIERS["OpenShell Policy Tiers\nrestricted ⊂ balanced ⊂ open\n(open — не используется в проде)"]:::tier

    subgraph HERMES["Hermes Profiles"]
        PH1["hermes-local\nlocal inference + git"]:::profile
        PH2["hermes-cloud\nоблачный провайдер + git"]:::profile
    end
    subgraph CCPROF["Claude Code Profile
⚠ claude-code — НЕТ в NemoClaw
tолько openclaw/manifest.yaml +
policy-пресеты из sandbox-templates/"]:::note
        PC1["openclaw sandbox base"]:::profile
    end

    TIERS --> HERMES & CCPROF

    subgraph HPR["Hermes Presets"]
        PR1["github/gitlab-hermes\nread-only git\nMR/PR via API only"]:::preset
        PR2["internal-api.yaml\nrouter :4000\ndocker-compose\nmemgraphrag :8000\nSTT :10301"]:::preset
        PR3["local-inference\n⚠ НЕ в presets/\nапстрим NemoClaw\n(vLLM :8088 / Ollama :11434)"]:::preset
    end
    subgraph CPR["Claude Code Presets"]
        PR4["claude-code-strict\nтолько api.anthropic.com\nтелеметрия / sentry вырезаны"]:::preset
        PR5["gitlab-claude-code\nполный git включая push"]:::preset
        PR6["web-reference-claude-code\nWebFetch → курируемый allowlist"]:::preset
    end

    PH1 & PH2 --> PR1 & PR2
    PH1 --> PR3
    PC1 --> PR4 & PR5 & PR6

    SSRF["SSRF-guard\nприватные сети блокированы по умолчанию\n10.0.0.0/8 · 172.16.0.0/12\n192.168.0.0/16 · 169.254.0.0/16"]:::guard
    EXPL["Точечные allowed-ip / endpoint правила\nдля каждого внутреннего сервиса"]:::rule
    CRED["Credential rewrite на egress\nтокены не видны процессу агента"]:::cred

    PR1 & PR2 & PR3 & PR4 & PR5 & PR6 --> SSRF --> EXPL --> CRED

    style HERMES fill:none,stroke:#3b82f6,stroke-width:2px
    style CCPROF fill:none,stroke:#3b82f6,stroke-width:2px
    style HPR    fill:none,stroke:#1e40af,stroke-width:1px,stroke-dasharray:4
    style CPR    fill:none,stroke:#1e40af,stroke-width:1px,stroke-dasharray:4
```

---

## 6 · Quality Gates & Testing

```mermaid
flowchart LR
    classDef src   fill:#1e293b,stroke:#475569,color:#f8fafc,stroke-width:2px
    classDef unit  fill:#166534,stroke:#4ade80,color:#fff,stroke-width:2px
    classDef gate  fill:#7f1d1d,stroke:#f87171,color:#fff,stroke-width:2px
    classDef ci    fill:#c2410c,stroke:#f97316,color:#fff,stroke-width:2px
    classDef ok    fill:#166534,stroke:#22c55e,color:#fff,stroke-width:2px
    classDef warn  fill:#92400e,stroke:#f59e0b,color:#fff,stroke-width:1px
    classDef fail  fill:#7f1d1d,stroke:#ef4444,color:#fff,stroke-width:2px
    classDef train fill:#6d28d9,stroke:#a78bfa,color:#fff,stroke-width:2px

    SRC["router/\nclassifier.py\nlitellm_config.yaml\nDockerfile"]:::src

    subgraph UNIT["Unit Tests · pytest · без GPU · 25 тестов"]
        TC["test_classifier.py\n11 тестов\nclassify() · routing\nAsyncMock HTTP"]:::unit
        TR["test_render_config.py\n14 тестов\nkey discovery · YAML"]:::unit
                TM["MemGraphRAG/tests/\ntest_api.py · 12 тестов\nTestClient · sys.modules mock\n(ещё 3 файла · 42 теста,\n54 total но не все collectable)"]:::unit
    end

    subgraph GATE["Pre-push Eval Gate · router only
.githooks/pre-push → eval/sandbox/run.sh
gate.py · 5 файлов, style: пропуск"]
        G1["gate.py\nbaseline comparison\navg correctness by tier"]:::gate
        G2["eval_router.py\ncheap/mid vs golden Sonnet"]:::gate
        G3["claude_cli.py\nclaude -p · ClaudeCliError"]:::gate
        SB(["claude-eval sandbox\nOpenShell isolated"]):::gate
        GW["⚠ INFRA-WARN\nclause-cli недоступен\npush не блокируется"]:::warn
        GP["✓ GATE: OK\n< 1.5 регрессии"]:::ok
        GF["✗ GATE: FAIL\nрегрессия ≥ 1.5\npass → fail curated"]:::fail
        G1 --> G2 --> G3 --> SB
        SB -->|session протухла| GW
        SB -->|в норме| GP
        SB -->|регрессия| GF
    end

    subgraph PIPE["GitLab CI · все репо"]
        L["lint"]:::ci
        EC["eval-config"]:::ci
        UT["unit-test"]:::ok
        B["kaniko build"]:::ci
        L --> EC --> UT --> B
    end

    subgraph TRAIN["LoRA Training · Phase 5 · S2L ✅"]
                GN["generate_queries.py\njudge-разметка\nclaude-cli\n(≠gen_dataset.py НЕТ)"]:::train
        TL["train_classifier.py\nLoRA SFT · GPU\nQwen3.5-0.8B"]:::train
        EC2["eval_classifier.py\naccuracy vs baseline"]:::train
        GN --> TL --> EC2
        TA["train_adapter.py\nS2L SFT · GPU\nflash-linear-attention"]:::train
        PS["prepare_security_data.py\njailbreak-detection\n2480 train / 827 val"]:::train
        PP["prepare_pii_data.py\npii-detection NER→type\n30k train / 3k val"]:::train
        PS & PP --> TA
        TA --> SEC["security-lora-v1 ✅\nloss=0.024 acc=98.9%"]:::train
        TA --> PII["pii-cleaner-lora-v1 ✅\nloss=0.034 acc=99.1%"]:::train
    end

    SRC --> UNIT & GATE & TRAIN
    UNIT --> PIPE

    style UNIT  fill:none,stroke:#166534,stroke-width:2px
    style GATE  fill:none,stroke:#7f1d1d,stroke-width:2px
    style PIPE  fill:none,stroke:#c2410c,stroke-width:2px
    style TRAIN fill:none,stroke:#6d28d9,stroke-width:2px
```
