# nemohermes_bks — Architecture Diagrams

Рендерится нативно в GitLab / GitHub. Для редактирования: [mermaid.live](https://mermaid.live)

**Цветовой код (единый для всех диаграмм):**
🟢 Агенты &nbsp;|&nbsp; 🔵 Роутер / K3s &nbsp;|&nbsp; 🟣 GPU / vLLM &nbsp;|&nbsp; 🟠 GitLab CI &nbsp;|&nbsp; 🩵 Облачные API &nbsp;|&nbsp; 🟡 Хранилище &nbsp;|&nbsp; 🔴 Security / Gate

---

## 1 · Обзор системы

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#f8fafc', 'primaryBorderColor': '#cbd5e1', 'lineColor': '#64748b', 'fontSize': '15px', 'fontFamily': 'ui-sans-serif, system-ui, sans-serif'}}}%%
flowchart LR
    classDef dev      fill:#1e293b,stroke:#0f172a,color:#f8fafc,stroke-width:2px
    classDef ci       fill:#ea580c,stroke:#9a3412,color:#fff,stroke-width:2px
    classDef registry fill:#9a3412,stroke:#7c2d12,color:#fff,stroke-width:2px
    classDef agent    fill:#16a34a,stroke:#14532d,color:#fff,stroke-width:2px
    classDef router   fill:#1d4ed8,stroke:#1e3a8a,color:#fff,stroke-width:2px
    classDef vllm     fill:#7c3aed,stroke:#4c1d95,color:#fff,stroke-width:2px
    classDef memory   fill:#d97706,stroke:#78350f,color:#fff,stroke-width:2px
    classDef cloud    fill:#0284c7,stroke:#0c4a6e,color:#fff,stroke-width:2px
    classDef external fill:#475569,stroke:#1e293b,color:#fff,stroke-width:2px

    DEV(["👨‍💻 Developer"]):::dev
    TG(["✈️ Telegram\nUsers"]):::external

    subgraph GITLAB["🦊 GitLab CE · 192.168.2.180:8929"]
        GL["CI Pipeline\nlint · test · build"]:::ci
        REG[("Registry\n:5050")]:::registry
        GL -->|"kaniko push"| REG
    end

    subgraph SAND["🛡️ OpenShell Sandbox  bks-production"]
        AG1["director-bot"]:::agent
        AG2["mkt-bot"]:::agent
        AG3["research"]:::agent
        AG4["analytics · structuring\ncontent · market-monitor\nexperiment"]:::agent
    end

    subgraph K3S["☸️ K3s Cluster · 192.168.2.180"]
        subgraph NS1["namespace: bks-router"]
            RT["🔵 router\n:30400"]:::router
            VLLM["🟣 vllm-classifier\nQwen3.5-0.8B · GPU"]:::vllm
        end
        subgraph NS2["namespace: memgraphrag"]
            MGR["🟡 MemGraphRAG\n:8000"]:::memory
            QDR["🟡 Qdrant\n:6333"]:::memory
        end
    end

    subgraph LLMS["☁️ Cloud LLM APIs"]
        NV["NVIDIA\ncheap · mid · large"]:::cloud
        AC["Anthropic\nclaude-sonnet"]:::cloud
    end

    DEV -->|"git push"| GL
    REG -.->|"imagePullPolicy: Always"| K3S

    TG --> AG1 & AG2 & AG3 & AG4
    AG1 & AG2 & AG3 & AG4 -->|"/v1/chat/completions"| RT
    RT -->|classify| VLLM
    RT -->|proxy tier| NV & AC
    AG3 -->|"episodes · retrieve"| MGR
    MGR <-->|"vectors"| QDR
```

---

## 2 · CI/CD Pipeline

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#fff7ed', 'primaryBorderColor': '#fed7aa', 'lineColor': '#ea580c', 'fontSize': '14px'}}}%%
flowchart TD
    classDef dev      fill:#1e293b,stroke:#0f172a,color:#fff,stroke-width:2px
    classDef stage    fill:#fff7ed,stroke:#ea580c,color:#9a3412,stroke-width:1px
    classDef ok       fill:#16a34a,stroke:#14532d,color:#fff,stroke-width:2px
    classDef reg      fill:#9a3412,stroke:#7c2d12,color:#fff,stroke-width:2px
    classDef runner   fill:#ea580c,stroke:#9a3412,color:#fff,stroke-width:1px
    classDef gate     fill:#dc2626,stroke:#7f1d1d,color:#fff,stroke-width:2px
    classDef warn     fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:1px

    PUSH(["git push\norigin main"]):::dev

    PUSH --> HOOK

    subgraph HOOK["pre-push hook  ·  router only"]
        H1{"файлы\nроутера\nменялись?"}:::stage
        H2{"style:\nкоммиты?"}:::stage
        H3["eval/gate.py\nclaude-eval sandbox"]:::gate
        HWARN["⚠️ INFRA-WARN\nсессия протухла\npush разрешён"]:::warn
        HOK["✅ GATE: OK"]:::ok
        HFAIL["🚫 GATE: FAIL\npush заблокирован"]:::gate
        H1 -->|нет| HOK
        H1 -->|да| H2
        H2 -->|да| HOK
        H2 -->|нет| H3
        H3 -->|"claude-cli недоступен"| HWARN
        H3 -->|"регрессия ≥ 1.5"| HFAIL
        H3 -->|"в норме"| HOK
    end

    HOK --> GL
    HWARN --> GL

    subgraph GL["🦊 GitLab CE"]
        subgraph PIPE_R["bks/router  pipeline"]
            R1["lint\nruff check+format"]:::stage
            R2["eval-config\nrender_litellm_config"]:::stage
            R3["unit-test\npytest · 25 тестов"]:::ok
            R4["build\nkaniko"]:::stage
            R1 --> R2 --> R3 --> R4
        end
        subgraph PIPE_M["bks/memgraphrag  pipeline"]
            M1["lint"]:::stage
            M2["unit-test\npytest · 12 тестов"]:::ok
            M3["build\nkaniko"]:::stage
            M1 --> M2 --> M3
        end
        subgraph PIPE_S["bks/sandbox-templates  pipeline"]
            S1["validate-presets"]:::ok
        end
    end

    subgraph RUNNERS["Runners"]
        RC["bks-docker-runner\ndocker executor · CPU"]:::runner
        RG["bks-gpu-runner\ndocker executor + nvidia CDI\ntags: gpu"]:::runner
    end
    GL -.->|"исполняет задачи"| RUNNERS

    REG[("📦 Registry\n192.168.2.180:5050\nbks/router:latest\nbks/memgraphrag:latest")]:::reg
    R4 --> REG
    M3 --> REG
```

---

## 3 · Agent Runtime

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#f0fdf4', 'primaryBorderColor': '#86efac', 'lineColor': '#64748b', 'fontSize': '14px'}}}%%
flowchart LR
    classDef tg      fill:#2563eb,stroke:#1e40af,color:#fff,stroke-width:2px
    classDef agent   fill:#16a34a,stroke:#14532d,color:#fff,stroke-width:2px
    classDef router  fill:#1d4ed8,stroke:#1e3a8a,color:#fff,stroke-width:2px
    classDef vllm    fill:#7c3aed,stroke:#4c1d95,color:#fff,stroke-width:2px
    classDef litellm fill:#1e40af,stroke:#1e3a8a,color:#fff,stroke-width:2px
    classDef cloud   fill:#0284c7,stroke:#0c4a6e,color:#fff,stroke-width:2px
    classDef memory  fill:#d97706,stroke:#78350f,color:#fff,stroke-width:2px
    classDef stt     fill:#475569,stroke:#1e293b,color:#fff,stroke-width:1px
    classDef web     fill:#475569,stroke:#1e293b,color:#fff,stroke-width:1px

    TGP(["✈️ Telegram\nпрод"]):::tg
    TGM(["✈️ Telegram\nmkt"]):::tg

    subgraph SAND["🛡️ OpenShell Sandbox  bks-production  ·  SSRF-guard enabled"]
        direction TB
        subgraph G1["auto tier"]
            DB["director-bot"]:::agent
            EXP["experiment"]:::agent
            MKT["mkt-bot"]:::agent
        end
        subgraph G2["mid tier"]
            MM["market-monitor"]:::agent
            ST["structuring"]:::agent
        end
        subgraph G3["large tier"]
            AN["analytics"]:::agent
            RS["research"]:::agent
            CN["content"]:::agent
        end
    end

    TGP --> DB & EXP
    TGM --> MKT & MM
    AN -.->|дайджест| TGM
    MM -.->|дайджест| TGM

    subgraph ROUTER["🔵 Router  ·  NodePort :30400"]
        CLS["classifier.py\n:4000"]:::router
        VLLM["vllm-classifier\nQwen3.5-0.8B · GPU\n:8000"]:::vllm
        LL["LiteLLM proxy\n:4001"]:::litellm
        CLS -->|">240K → mid\nauto → classify"| VLLM
        VLLM -->|"tier: cheap/mid/large"| CLS
        CLS -->|proxy| LL
    end

    DB & EXP & MKT & MM & AN & ST & RS & CN -->|"/v1/chat/completions"| CLS

    subgraph CLOUD["☁️ Cloud LLM APIs"]
        NV["NVIDIA API\ncheap → nemotron-nano-30b\nmid  → nemotron-super-49b\nlarge → nemotron-ultra-550b"]:::cloud
        AC["Anthropic API\nlarge fallback\nclaude-sonnet-4-6"]:::cloud
    end
    LL --> NV & AC

    MGR["🟡 MemGraphRAG\nK3s :8000\n/api/episodes\n/api/retrieve"]:::memory
    WEB(["🌐 Интернет\nnous-web"]):::web
    STT["🎙️ STT :10301\nfaster-whisper\nlarge-v3"]:::stt

    RS -->|"code_execution"| MGR
    RS & MM -->|"web_search/extract"| WEB
    DB & MKT -.->|"опц. голос"| STT
```

---

## 4 · K3s Infrastructure

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#eff6ff', 'primaryBorderColor': '#bfdbfe', 'lineColor': '#3b82f6', 'fontSize': '14px'}}}%%
flowchart TD
    classDef reg      fill:#9a3412,stroke:#7c2d12,color:#fff,stroke-width:2px
    classDef deploy   fill:#1d4ed8,stroke:#1e3a8a,color:#fff,stroke-width:2px
    classDef vllm     fill:#7c3aed,stroke:#4c1d95,color:#fff,stroke-width:2px
    classDef svc      fill:#0369a1,stroke:#0c4a6e,color:#fff,stroke-width:1px
    classDef pvc      fill:#d97706,stroke:#78350f,color:#fff,stroke-width:1px
    classDef secret   fill:#dc2626,stroke:#7f1d1d,color:#fff,stroke-width:1px
    classDef cm       fill:#475569,stroke:#1e293b,color:#fff,stroke-width:1px
    classDef plugin   fill:#7c3aed,stroke:#4c1d95,color:#fff,stroke-width:1px
    classDef agent    fill:#16a34a,stroke:#14532d,color:#fff,stroke-width:2px
    classDef ext      fill:#475569,stroke:#1e293b,color:#fff,stroke-width:2px

    REG[("📦 Registry\n192.168.2.180:5050")]:::reg
    MODELS[("💾 hostPath\n/root/models\nQwen3.5-0.8B")]:::pvc

    subgraph K3S["☸️ K3s  ·  192.168.2.180"]
        NVP["nvidia-device-plugin\nDaemonSet · kube-system\n→ nvidia.com/gpu=1"]:::plugin

        subgraph NSR["namespace: bks-router"]
            CM["ConfigMap\nlitellm_config.base.yaml"]:::cm
            SEC_R["Secret\nrouter-apikeys\nNVIDIA_API_KEY_N\nANTHROPIC_API_KEY"]:::secret

            VLLM_D["Deployment\nvllm-classifier\nvllm-openai:v0.9.0\nnvidia.com/gpu: 1"]:::vllm
            SVC_V["Service\nClusterIP :8000"]:::svc

            ROUTER_D["Deployment\nrouter\nbks/router:latest\nclassifier.py + litellm\nsupervisord"]:::deploy
            SVC_R["Service\nNodePort 30400→4000"]:::svc

            CM & SEC_R --> ROUTER_D
            VLLM_D --> SVC_V
            SVC_V -->|"vllm-classifier.bks-router.svc:8000/v1"| ROUTER_D
            ROUTER_D --> SVC_R
        end

        subgraph NSM["namespace: memgraphrag"]
            SEC_Q["Secret\nqdrant-apikey"]:::secret
            SEC_M["Secret\nmemgraphrag-apikey"]:::secret
            PVC_Q[("PVC qdrant-data\n20 Gi")]:::pvc
            PVC_M[("PVC memgraphrag-data\n10 Gi")]:::pvc

            QD_D["Deployment\nqdrant\nqdrant:v1.9.7"]:::deploy
            SVC_Q["Service\nClusterIP :6333"]:::svc

            MGR_D["Deployment\nmemgraphrag\nbks/memgraphrag:latest\nContriever · igraph · SQLite\nNAMESPACES: prod·mkt"]:::deploy
            SVC_M["Service\nClusterIP :8000"]:::svc

            SEC_Q --> QD_D
            PVC_Q --> QD_D
            QD_D --> SVC_Q
            SVC_Q -->|"qdrant.memgraphrag.svc:6333"| MGR_D
            SEC_M & PVC_M --> MGR_D
            MGR_D --> SVC_M
        end

        NVP -.->|"advertises GPU"| VLLM_D
    end

    REG -->|"imagePullPolicy: Always"| VLLM_D & ROUTER_D & MGR_D
    MODELS -->|"hostPath mount"| VLLM_D

    AGENTS(["🛡️ Agents\nOpenShell sandbox"]):::agent
    AGENTS -->|":30400"| SVC_R
    AGENTS -->|"memgraphrag.memgraphrag.svc:8000"| SVC_M

    style NSR fill:#dbeafe,stroke:#1d4ed8,stroke-width:2px,color:#1e3a8a
    style NSM fill:#fef9c3,stroke:#ca8a04,stroke-width:2px,color:#713f12
```

---

## 5 · Security Layers

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#faf5ff', 'primaryBorderColor': '#d8b4fe', 'lineColor': '#7c3aed', 'fontSize': '14px'}}}%%
flowchart TD
    classDef tier     fill:#7c3aed,stroke:#4c1d95,color:#fff,stroke-width:2px
    classDef profile  fill:#1d4ed8,stroke:#1e3a8a,color:#fff,stroke-width:2px
    classDef preset   fill:#0369a1,stroke:#0c4a6e,color:#fff,stroke-width:1px
    classDef guard    fill:#dc2626,stroke:#7f1d1d,color:#fff,stroke-width:2px
    classDef rule     fill:#b45309,stroke:#78350f,color:#fff,stroke-width:1px
    classDef cred     fill:#16a34a,stroke:#14532d,color:#fff,stroke-width:2px

    TIERS["OpenShell Policy Tiers\nrestricted ⊂ balanced ⊂ open\n(open — не используется в проде)"]:::tier

    subgraph HERMES["Hermes Agent Profiles"]
        PH1["hermes-local\nlocal-inference + git"]:::profile
        PH2["hermes-cloud\nоблачный провайдер + git"]:::profile
    end

    subgraph CC["Claude Code Profiles"]
        PC1["claude-code\nsandbox base"]:::profile
    end

    TIERS --> HERMES & CC

    subgraph PRESETS_H["Hermes Presets  (opt-in)"]
        PR1["github-hermes\ngitlab-hermes\nread-only git\nMR/PR via API only"]:::preset
        PR2["internal-api.yaml\nallowlist:\n• router :4000/:30400\n• memgraphrag :8000\n• STT :10301"]:::preset
        PR3["local-inference\nvLLM/Ollama :8088/:11434"]:::preset
    end

    subgraph PRESETS_CC["Claude Code Presets  (opt-in)"]
        PR4["claude-code-strict\nтолько api.anthropic.com\nтелеметрия/sentry вырезаны"]:::preset
        PR5["gitlab-claude-code\nполный git включая push"]:::preset
        PR6["web-reference-claude-code\nWebFetch → курируемый allowlist"]:::preset
    end

    PH1 & PH2 --> PR1 & PR2
    PH1 --> PR3
    PC1 --> PR4 & PR5 & PR6

    SSRF["🚫 SSRF-guard\nприватные сети блокированы по умолчанию\n169.254.0.0/16 · 10.0.0.0/8 · 172.16.0.0/12 · 192.168.0.0/16"]:::guard

    PR1 & PR2 & PR3 & PR4 & PR5 & PR6 --> SSRF

    EXPLICIT["Точечные allowed-ip / endpoint правила\nдля каждого внутреннего сервиса"]:::rule
    CRED["Credential rewrite на egress\nтокены не попадают\nв память процесса агента"]:::cred

    SSRF --> EXPLICIT --> CRED
```

---

## 6 · Quality Gates & Testing

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#fff7ed', 'primaryBorderColor': '#fed7aa', 'lineColor': '#64748b', 'fontSize': '14px'}}}%%
flowchart LR
    classDef src    fill:#1e293b,stroke:#0f172a,color:#fff,stroke-width:2px
    classDef unit   fill:#16a34a,stroke:#14532d,color:#fff,stroke-width:2px
    classDef eval   fill:#dc2626,stroke:#7f1d1d,color:#fff,stroke-width:2px
    classDef ci     fill:#ea580c,stroke:#9a3412,color:#fff,stroke-width:2px
    classDef train  fill:#7c3aed,stroke:#4c1d95,color:#fff,stroke-width:2px
    classDef warn   fill:#fef3c7,stroke:#d97706,color:#78350f,stroke-width:1px
    classDef pass   fill:#dcfce7,stroke:#16a34a,color:#14532d,stroke-width:1px

    SRC["📁 router/\nclassifier.py\nlitellm_config.yaml\nDockerfile"]:::src

    subgraph UNIT["Unit Tests  ·  pytest  ·  без GPU"]
        TC["test_classifier.py\n11 тестов\nclassify() · routing\nAsyncMock HTTP"]:::unit
        TR["test_render_config.py\n14 тестов\nkey discovery\ndeployments · YAML"]:::unit
        TM["MemGraphRAG/tests/\ntest_api.py\n12 тестов\nTestClient · sys.modules mock"]:::unit
    end

    subgraph GATE["Pre-push Eval Gate  ·  router only"]
        direction TB
        G1["gate.py\nbaseline comparison\navg correctness by tier"]:::eval
        G2["eval_router.py\nbatch: cheap/mid vs\nSonnet golden answer"]:::eval
        G3["claude_cli.py\nclaude -p wrapper"]:::eval
        G4(["claude-eval sandbox\nOpenShell isolated"]):::eval
        GWARN["⚠️ INFRA-WARN\nclaude-cli недоступен\npush не блокируется"]:::warn
        GPASS["✅ GATE: OK\nрегрессия < 1.5"]:::pass
        GFAIL["🚫 GATE: FAIL\nрегрессия ≥ 1.5 ИЛИ\ncurated pass→fail"]:::eval
        G1 --> G2 --> G3 --> G4
        G4 -->|"exit 1 (сессия)"| GWARN
        G4 -->|"в норме"| GPASS
        G4 -->|"регрессия"| GFAIL
    end

    subgraph PIPELINE["GitLab CI  ·  все три репо"]
        L["lint\nruff check\nruff format"]:::ci
        EC["eval-config\nrender_litellm_config.py"]:::ci
        UT["unit-test\npytest"]:::unit
        B["kaniko build\n→ registry push"]:::ci
        L --> EC --> UT --> B
    end

    subgraph TRAIN["LoRA Training  ·  Phase 5  ·  opt-in"]
        GEN["gen_dataset.py\njudge-разметка\nclaude-cli"]:::train
        TLR["train_classifier.py\nLoRA SFT\nQwen3.5-0.8B · GPU"]:::train
        ECLS["eval_classifier.py\naccuracy vs baseline"]:::train
        GEN --> TLR --> ECLS
    end

    SRC --> UNIT & GATE & TRAIN
    UNIT --> PIPELINE
```
