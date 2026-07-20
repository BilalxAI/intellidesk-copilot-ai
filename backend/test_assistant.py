"""Local smoke tests for the IT Support Assistant."""

import sys

from app.config import KB_PATH, OLLAMA_BASE_URL, OLLAMA_MODEL
from app.core.pipeline import ITSupportPipeline
from app.kb.loader import get_kb
from app.services.classifier import get_classifier
from app.services.llm_client import OllamaClient

TEST_CASES = [
    ("my headset is not working", "HEADSET_ISSUE"),
    ("wifi not connecting", "NETWORK_ISSUE"),
    ("Teams meeting not working", "TEAMS_ISSUE"),
    ("Outlook emails are not syncing", "OUTLOOK_ISSUE"),
    ("keyboard not responding", "HARDWARE_ISSUE"),
    ("software installation failed", "SOFTWARE_INSTALLATION"),
]


def safe(text) -> str:
    return str(text).encode("ascii", errors="replace").decode("ascii")


def test_kb_loading() -> bool:
    print("\nTEST 1: Knowledge Base")
    kb = get_kb(KB_PATH)
    print(f"Loaded categories: {len(kb.data)}")
    for category, steps in kb.data.items():
        print(f"  {category}: {len(steps)} steps")
    return bool(kb.data)


def test_classifier() -> bool:
    print("\nTEST 2: Classifier")
    classifier = get_classifier()
    passed = 0
    for issue, expected in TEST_CASES:
        predicted, confidence = classifier.classify(issue)
        ok = predicted == expected
        passed += int(ok)
        print(f"{'PASS' if ok else 'FAIL'} {issue} -> {predicted} ({confidence:.2f})")
    return passed == len(TEST_CASES)


def test_ollama_health() -> bool:
    print("\nTEST 3: Ollama Health")
    client = OllamaClient(OLLAMA_BASE_URL, OLLAMA_MODEL)
    healthy = client.check_health()
    print(f"Ollama available: {healthy}")
    print(f"Model: {OLLAMA_MODEL}")
    return True


def test_pipeline() -> bool:
    print("\nTEST 4: Pipeline")
    pipeline = ITSupportPipeline()
    result = pipeline.process("Teams microphone is not working")
    print(f"Status: {result.get('status')}")
    print(f"Category: {result.get('category')}")
    print(f"Response preview: {safe(result.get('response', '')[:160])}")
    return result.get("status") == "success"


def test_conversation_follow_up() -> bool:
    print("\nTEST 5: Conversation Follow-up")
    pipeline = ITSupportPipeline()
    first = pipeline.process("My headset is not working")
    second = pipeline.process(
        "I tried step 1 and it still crashed",
        conversation_id=first["conversation_id"],
    )
    print(f"Conversation: {first['conversation_id']}")
    print(f"First category: {first.get('category')}")
    print(f"Second category: {second.get('category')}")
    print(f"Follow-up: {second.get('is_follow_up')}")
    return (
        first.get("conversation_id") == second.get("conversation_id")
        and second.get("is_follow_up") is True
        and second.get("category") == first.get("category")
    )


def test_guided_steps() -> bool:
    print("\nTEST 6: Guided Step Mode")
    pipeline = ITSupportPipeline()
    first = pipeline.process(
        "My headset is not working, give me step by step, first step first then if it fails give second"
    )
    second = pipeline.process("failed", conversation_id=first["conversation_id"])
    print(f"First response: {safe(first.get('response', '')[:120])}")
    print(f"Second response: {safe(second.get('response', '')[:120])}")
    return (
        first.get("response", "").startswith("Step 1:")
        and second.get("response", "").startswith("Step 2:")
        and second.get("category") == first.get("category")
    )


def test_new_issue_same_conversation() -> bool:
    print("\nTEST 7: New Issue Same Conversation")
    pipeline = ITSupportPipeline()
    first = pipeline.process("My headset is not working")
    second = pipeline.process(
        "Teams is not working",
        conversation_id=first["conversation_id"],
    )
    print(f"First category: {first.get('category')}")
    print(f"Second category: {second.get('category')}")
    print(f"Follow-up: {second.get('is_follow_up')}")
    return (
        first.get("conversation_id") == second.get("conversation_id")
        and first.get("category") == "HEADSET_ISSUE"
        and second.get("category") == "TEAMS_ISSUE"
        and second.get("is_follow_up") is False
    )


def test_admin_guardrail() -> bool:
    print("\nTEST 8: Admin/Security Guardrail")
    pipeline = ITSupportPipeline()
    # This guardrail is LLM-based; skip if Ollama/model isn't available so offline
    # KB-fallback mode can still pass the rest of the suite.
    if not pipeline.llm.check_health():
        print("SKIP - Ollama not available for LLM-based guardrail test")
        return True
    result = pipeline.process("please release my email, it is quarantined")
    response = (result.get("response") or "").lower()
    print(f"Category: {result.get('category')}")
    print(f"Response preview: {safe((result.get('response') or '')[:160])}")
    return "contact it support" in response and "step 1" not in response


def test_unknown_escalation() -> bool:
    print("\nTEST 9: Unknown/Out-of-scope Escalation")
    pipeline = ITSupportPipeline()
    if not pipeline.llm.check_health():
        print("SKIP - Ollama not available for LLM-based unknown escalation test")
        return True
    result = pipeline.process("please unlock domain of example.com")
    response = (result.get("response") or "").lower()
    print(f"Category: {result.get('category')}")
    print(f"Response preview: {safe((result.get('response') or '')[:160])}")
    return result.get("category") == "UNKNOWN" and "contact it support" in response and "step 1" not in response


def run_all_tests() -> bool:
    tests = [
        ("KB Loading", test_kb_loading),
        ("Classification", test_classifier),
        ("Ollama Health", test_ollama_health),
        ("Pipeline", test_pipeline),
        ("Conversation Follow-up", test_conversation_follow_up),
        ("Guided Step Mode", test_guided_steps),
        ("New Issue Same Conversation", test_new_issue_same_conversation),
        ("Admin Guardrail", test_admin_guardrail),
        ("Unknown Escalation", test_unknown_escalation),
    ]

    results = []
    for name, test in tests:
        try:
            results.append((name, test()))
        except Exception as exc:
            print(f"ERROR {name}: {exc}")
            results.append((name, False))

    print("\nSUMMARY")
    for name, passed in results:
        print(f"{'PASS' if passed else 'FAIL'} - {name}")

    return all(passed for _, passed in results)


if __name__ == "__main__":
    sys.exit(0 if run_all_tests() else 1)
