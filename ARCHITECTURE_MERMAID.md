# nemohermes_bks — схема взаимодействия (Mermaid)

Та же схема, что в [ARCHITECTURE.md](./ARCHITECTURE.md), но в формате
блок-диаграмм Mermaid. Рендерится нативно на GitHub/GitLab, либо через
[mermaid.live](https://mermaid.live) / VS Code-плагин Markdown Preview Mermaid.

---

## 0. CI/CD инфраструктура (GitLab CE)

```mermaid
flowchart TD
    DEV["git push origin main"]

    DEV --> GL["GitLab CE 19.1.1\n192.168.2.180:8929"]

    subgraph CI["CI Pipeline (per repo)"]
        LINT["lint\nruff check + format"]
        EVALCFG["eval-config\nrender_litellm_config.py"]
        UT["unit-test\npytest (без GPU / без Qdrant)"]
        BUILD["build\nkaniko → registry push"]
        LINT --> EVALCFG --> UT --> BUILD
    end

    GL --> CI

    subgraph RUNNERS["GitLab Runners"]
        CPU["bks-docker-runner\ndocker executor\nlint / unit-test / build"]
        GPU["bks-gpu-runner\ndocker executor + nvidia CDI\ntags: [gpu]\nvLLM smoke / LoRA eval"]
    end
    CI --> RUNNERS

    BUILD --> REG["Container Registry\n192.168.2.180:5050\nbks/router:latest\nbks/memgraphrag:latest"]

    PREPUSH["pre-push hook\neval/gate.py\n(claude-eval sandbox)"] -.->|"блокирует при регрессии\n[INFRA-WARN] при стухшей сессии"| DEV
```

---

## 1. Контур деплоя (хост → sandbox)

```mermaid
flowchart TD
    ENV["deploy/.env"] --> SETUP["setup.sh"]

    SETUP --> S1["1. nemohermes onboard --agent hermes"]
    S1 --> SANDBOX[("sandbox bks-production\nNemoClaw / OpenShell")]

    SETUP --> S2["2. openshell policy update\n(white-list egress)"]
    S2 --> EP1["api.telegram.org:443"]
    S2 --> EP2["host.openshell.internal:4000 / :30400\nLLM Router"]
    S2 --> EP3["host.openshell.internal:10301\nSTT"]
    S2 --> EP4["host.openshell.internal:11434 / 8088\nlocal-inference (опц.)"]

    SETUP --> S3["3. openshell provider create\nTelegram-токены"]
    SETUP --> S4["4. openshell provider create\nMemGraphRAG API key"]

    SETUP --> S5["5. sync-profiles.sh"]
    S5 --> PROFILES["копирует bksamotsvety/profiles/*\nconfig.yaml, SOUL.md, skills/"]

    SETUP --> S6["6. start-gateways.sh"]
    S6 --> GW["hermes gateway run --profile X\n(1 процесс на профиль)"]
    GW --> SANDBOX
```

---

## 2. Рантайм: 8 профилей, роутер, внешние сервисы

```mermaid
flowchart LR
    TGP(["Telegram\nпрод-группа"])
    TGM(["Telegram\nmkt-группа"])

    subgraph SANDBOX["sandbox bks-production (OpenShell, SSRF-guard)"]
        DB["director-bot\nmodel: auto"]
        EXP["experiment\nmodel: auto"]
        MKT["mkt-bot\nmodel: auto"]
        MM["market-monitor\nmodel: mid"]
        AN["analytics\nmodel: large"]
        ST["structuring\nmodel: mid"]
        RS["research\nmodel: large"]
        CN["content\nmodel: large"]
    end

    TGP --> DB & EXP
    TGM --> MKT & MM
    AN -.->|дайджест| TGM
    MM -.->|дайджест| TGM

    subgraph ROUTER["router (хост: docker-compose → K3s NodePort 30400)"]
        CLS["classifier.py :4000\nmodel==auto → tier\n>240K симв. → mid без классификации"]
        VLLM["vllm-classifier :8000\nQwen3.5-0.8B (GPU)\n--enable-auto-tool-choice\n--tool-call-parser qwen3_xml"]
        LITELLM["LiteLLM proxy :4001"]
        CLS -->|classify| VLLM
        CLS -->|proxy| LITELLM
    end

    DB & EXP & MKT & MM & AN & ST & RS & CN --> CLS

    subgraph CLOUD["Cloud LLM APIs"]
        NV["integrate.api.nvidia.com\ncheap · mid · large nemotron"]
        AC["api.anthropic.com\nclaude-sonnet (fallback large)"]
    end
    LITELLM --> NV & AC

    MGR["MemGraphRAG\nK3s ClusterIP :8000\n/api/episodes · /api/retrieve"]
    WEB(["Интернет\n(пресет nous-web)"])
    STT["STT host:10301\nfaster-whisper-large-v3"]

    RS -->|code_execution| MGR
    RS & MM -->|web_search/extract| WEB
    DB & MKT -.->|опц. голос| STT
```

---

## 2.5 K3s: целевой деплой сервисов

> Статус 2026-06-28: манифесты готовы, K3s ещё не установлен.
> Установка: `sudo bash /home/admin/servers/k3s/install.sh`

```mermaid
flowchart TD
    REG["Registry\n192.168.2.180:5050"] -->|imagePullPolicy: Always| K3S

    subgraph K3S["K3s (192.168.2.180)"]

        subgraph NS_ROUTER["namespace: bks-router"]
            VLLM_D["Deployment: vllm-classifier\nvllm/vllm-openai:v0.9.0\nnvidia.com/gpu=1\nhostPath /root/models"]
            ROUTER_D["Deployment: router\nbks/router:latest\nConfigMap: litellm_config.base.yaml\nSecret: router-apikeys"]
            SVC_VLLM["Service: ClusterIP :8000"]
            SVC_ROUTER["Service: NodePort 30400 → :4000"]
            VLLM_D --> SVC_VLLM -->|http://vllm-classifier.bks-router.svc:8000/v1| ROUTER_D
            ROUTER_D --> SVC_ROUTER
        end

        subgraph NS_MGR["namespace: memgraphrag"]
            QD["Deployment: qdrant\nqdrant/qdrant:v1.9.7\nPVC 20Gi"]
            MGR_D["Deployment: memgraphrag\nbks/memgraphrag:latest\nPVC 10Gi\nNAMESPACES: prod:bks_prod,mkt:bks_mkt"]
            SVC_QD["Service: ClusterIP :6333"]
            SVC_MGR["Service: ClusterIP :8000"]
            QD --> SVC_QD -->|http://qdrant.memgraphrag.svc:6333| MGR_D
            MGR_D --> SVC_MGR
        end

        NV_PLUGIN["nvidia-device-plugin\nDaemonSet (kube-system)"]
        NV_PLUGIN -.->|nvidia.com/gpu=1| VLLM_D
    end

    AGENTS["sandbox bks-production\n(агенты)"] -->|http://192.168.2.180:30400| SVC_ROUTER
    AGENTS -->|http://memgraphrag.memgraphrag.svc:8000| SVC_MGR
```

---

## 3. Слой безопасности (OpenShell / NemoClaw / sandbox-templates)

```mermaid
flowchart TD
    TIER["OpenShell tiers:\nrestricted ⊂ balanced ⊂ open\n(open — не используется в проде)"]

    TIER --> PROF1["hermes-local\nbase: hermes + local-inference"]
    TIER --> PROF2["hermes-cloud\nbase: hermes + облачный провайдер"]
    TIER --> PROF3["claude-code\nbase: sandbox base"]

    PROF1 & PROF2 --> P_HG["github-hermes / gitlab-hermes\nread-only git + MR/PR через API"]
    PROF1 & PROF2 --> P_IA["internal-api.yaml\nточечный allowlist внутренних API\n(router, MemGraphRAG, STT)"]

    PROF3 --> P_CC1["claude-code-strict\nтелеметрия/sentry вырезаны\nтолько api.anthropic.com"]
    PROF3 --> P_CC2["github-claude-code / gitlab-claude-code\nполный git, включая push"]
    PROF3 --> P_CC3["web-reference-claude-code\nWebFetch только на курируемый allowlist"]

    P_HG & P_IA & P_CC1 & P_CC2 & P_CC3 --> SSRF["SSRF-guard:\nприватные сети блокированы по умолчанию"]
    SSRF --> EXP["Точечные allowed-ip / endpoint правила"]
    EXP --> CRED["Credential rewrite на egress —\nтокены не попадают в память процесса агента"]
```

---

## 4. Качество и CI роутера

```mermaid
flowchart LR
    SRC["router/\nclassifier.py\nlitellm_config.yaml\nDockerfile"]

    subgraph TESTS["Тесты (pytest, без GPU)"]
        TC["test_classifier.py\n11 тестов\nclassify() · /v1/chat/completions\nAsyncMock HTTP"]
        TR["test_render_config.py\n14 тестов\nkey discovery · deployments\nYAML output"]
    end

    subgraph EVAL["Eval gate (pre-push hook)"]
        GATE["gate.py\nbaseline comparison\navg correctness by tier"]
        CLI["claude_cli.py\nclaude -p wrapper\nClaudeCliError → INFRA-WARN"]
        GATE --> CLI
    end

    subgraph TRAIN["Training (Phase 5, opt-in)"]
        GEN["gen_dataset.py\njudge-разметка через claude-cli"]
        LORA["train_classifier.py\nLoRA SFT Qwen3.5-0.8B"]
        EVALCLS["eval_classifier.py\naccuracy vs baseline"]
        GEN --> LORA --> EVALCLS
    end

    SRC --> TESTS
    SRC --> EVAL
    SRC --> TRAIN

    subgraph PIPELINE["GitLab CI"]
        L["lint"] --> EC["eval-config"] --> UT["unit-test"] --> B["kaniko build"]
    end

    TESTS --> PIPELINE
```
