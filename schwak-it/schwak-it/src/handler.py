"""
SCHWAK-IT CONSULTANCY — AI Consultation Lambda
Powered by Anthropic Claude | Elon Musk persona
Domains: Cybersecurity · Python · Linux · Web Dev · AI/ML · Kubernetes · Data Science
"""

import json
import os
import logging
import urllib.request

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ssm_client = boto3.client("ssm")
_cached_api_key = None


def get_api_key() -> str:
    """Retrieve Anthropic API key from SSM Parameter Store (cached across warm starts)."""
    global _cached_api_key
    if _cached_api_key:
        return _cached_api_key
    try:
        param_name = os.environ["ANTHROPIC_API_KEY_PARAM"]
        response = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
        _cached_api_key = response["Parameter"]["Value"]
        return _cached_api_key
    except ClientError as e:
        logger.error(f"Failed to retrieve API key from SSM: {e}")
        raise RuntimeError("API key configuration error") from e


SYSTEM_PROMPT = """You are the AI brain of SCHWAK-IT CONSULTANCY — a no-nonsense, elite tech consulting firm.

Your persona: Think Elon Musk energy. Brutally direct. First-principles thinker.
You don't pad answers with fluff. You get to the point immediately, challenge weak assumptions,
and deliver the most efficient path to a solution. You're confident to the point of being blunt,
but you back every claim with substance.

Your firm's domains of expertise:
1. CYBERSECURITY — Offensive & defensive security, threat modeling, SIEM, zero-trust architecture,
   penetration testing, incident response, SOC operations, secure coding practices, CVE analysis,
   firewall configuration, IDS/IPS, cryptography, and compliance (NIST, ISO 27001, SOC2).

2. PYTHON — Production-grade Python development, automation, scripting, REST APIs (FastAPI/Flask/Django),
   data pipelines, security tooling in Python, performance optimization, packaging, testing,
   and DevOps scripting.

3. LINUX — System administration, hardening, kernel tuning, bash scripting, process management,
   networking, storage, cron jobs, systemd, package management (apt/yum/dnf/pacman),
   server configuration, and CLI mastery.

4. WEB APPLICATION DEVELOPMENT — Full-stack development, frontend (React/Vue/Next.js),
   backend (Node/Python/Go), REST & GraphQL APIs, cloud-native apps, database design,
   authentication/authorization, performance, and security-first development.

5. ARTIFICIAL INTELLIGENCE & MACHINE LEARNING — LLM integration, RAG pipelines, fine-tuning,
   prompt engineering, agent frameworks (LangChain, LlamaIndex, AutoGen), model evaluation,
   embeddings, vector databases (Pinecone, Weaviate, pgvector), PyTorch, TensorFlow, Hugging Face,
   production ML inference, and AI product architecture.

6. KUBERNETES & CONTAINERS — Cluster design and administration, Helm chart authoring,
   GitOps with ArgoCD and Flux, horizontal/vertical pod autoscaling, resource quotas,
   service meshes (Istio, Linkerd), multi-cloud Kubernetes (EKS, GKE, AKS), Docker,
   container security, network policies, persistent volumes, and observability (Prometheus, Grafana).

7. DATA SCIENCE — End-to-end data pipelines, ETL/ELT, exploratory data analysis, statistical modeling,
   time-series forecasting, feature engineering, Pandas, NumPy, SQL at scale, Apache Spark,
   data visualization (Matplotlib, Plotly, Tableau), A/B testing, business intelligence,
   and productionizing data workflows.

Consulting style rules:
- Open with the direct answer or diagnosis immediately — no preambles
- Use bullet points or numbered steps for technical instructions
- Call out bad practices when you see them
- Provide code snippets when they're the clearest explanation
- If a question is outside your 7 domains, redirect firmly: "That's not what we do here."
- Occasionally drop a signature phrase like "First principles:" or "Here's the brutal truth:"
  to frame key insights
- Keep responses focused and actionable — no textbook filler

Firm motto: "We don't consult. We solve."
"""


def build_cors_headers() -> dict:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Headers": "Content-Type,X-Amz-Date,Authorization,X-Api-Key",
        "Access-Control-Allow-Methods": "POST,GET,OPTIONS",
        "Content-Type": "application/json",
    }


def call_claude(messages: list, max_tokens: int = 1500) -> str:
    """Call Anthropic Claude API, preferring the SDK from the Lambda layer, falling back to urllib."""
    api_key = get_api_key()

    try:
        import anthropic  # noqa: F401 — available via layer

        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return response.content[0].text

    except ImportError:
        logger.warning("anthropic SDK not found in layer; using urllib fallback")
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": max_tokens,
            "system": SYSTEM_PROMPT,
            "messages": messages,
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=50) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["content"][0]["text"]


def detect_domain(message: str) -> str:
    """Heuristically tag the consultation domain for analytics."""
    msg = message.lower()
    if any(k in msg for k in ["hack", "vuln", "cve", "pentest", "firewall", "exploit",
                                "malware", "siem", "threat", "encrypt", "security",
                                "zero-day", "payload", "xss", "sql injection", "nmap"]):
        return "cybersecurity"
    if any(k in msg for k in ["kubernetes", "k8s", "kubectl", "helm", "pod", "cluster",
                                "deployment", "ingress", "argocd", "flux", "istio",
                                "container", "docker", "namespace", "daemonset", "statefulset"]):
        return "kubernetes"
    if any(k in msg for k in ["machine learning", "ml", "llm", "rag", "embedding", "vector",
                                "fine-tun", "pytorch", "tensorflow", "hugging face", "langchain",
                                "agent", "prompt", "model", "neural", "inference", "gpt", "bert"]):
        return "artificial-intelligence"
    if any(k in msg for k in ["data science", "pandas", "numpy", "spark", "etl", "pipeline",
                                "forecast", "regression", "classification", "visualization",
                                "matplotlib", "plotly", "tableau", "a/b test", "dataset",
                                "feature engineering", "statistics", "correlation"]):
        return "data-science"
    if any(k in msg for k in ["python", "pip", "flask", "django", "fastapi", "pandas",
                                "asyncio", "decorator", "lambda", "class", "def ", ".py"]):
        return "python"
    if any(k in msg for k in ["linux", "bash", "shell", "chmod", "grep", "awk", "sed",
                                "systemd", "cron", "kernel", "ubuntu", "debian", "centos",
                                "sudo", "apt", "yum", "dnf", "ssh", "terminal"]):
        return "linux"
    if any(k in msg for k in ["react", "vue", "next", "html", "css", "javascript", "api",
                                "rest", "graphql", "frontend", "backend", "node", "express",
                                "webpack", "typescript", "http", "websocket"]):
        return "web-development"
    return "general"


def lambda_handler(event: dict, context) -> dict:
    headers = build_cors_headers()
    http_method = event.get("httpMethod", "GET")
    path = event.get("path", "/")

    if http_method == "OPTIONS":
        return {"statusCode": 200, "headers": headers, "body": ""}

    if path == "/health" or (path == "/" and http_method == "GET"):
        return {
            "statusCode": 200,
            "headers": headers,
            "body": json.dumps({
                "status": "operational",
                "service": "SCHWAK-IT CONSULTANCY",
                "domains": ["cybersecurity", "python", "linux", "web-development",
                            "artificial-intelligence", "kubernetes", "data-science"],
                "engine": "Claude (Anthropic)",
                "motto": "We don't consult. We solve.",
            }),
        }

    if path == "/consult" and http_method == "POST":
        try:
            body = json.loads(event.get("body") or "{}")
        except json.JSONDecodeError:
            return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "Invalid JSON body"})}

        message = (body.get("message") or "").strip()
        history = body.get("history", [])

        if not message:
            return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "Message field is required"})}
        if len(message) > 4000:
            return {"statusCode": 400, "headers": headers, "body": json.dumps({"error": "Message exceeds 4000 character limit"})}

        messages = []
        for turn in history[-10:]:
            role = turn.get("role")
            content = turn.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": message})

        try:
            reply = call_claude(messages)
            logger.info(f"Consultation served | chars_in={len(message)} chars_out={len(reply)}")
            return {
                "statusCode": 200,
                "headers": headers,
                "body": json.dumps({"reply": reply, "domain_hint": detect_domain(message)}),
            }
        except RuntimeError as e:
            logger.error(f"Config error: {e}")
            return {"statusCode": 500, "headers": headers, "body": json.dumps({"error": "Service configuration error. Contact admin."})}
        except Exception as e:
            logger.error(f"Claude API error: {e}")
            return {"statusCode": 502, "headers": headers, "body": json.dumps({"error": "AI engine temporarily unavailable. Retry in a moment."})}

    return {"statusCode": 404, "headers": headers, "body": json.dumps({"error": "Route not found"})}
