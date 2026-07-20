"""
Main pipeline orchestration logic.
"""

import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from ..config import (
    KB_PATH,
    OLLAMA_BASE_URL,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT,
    LLM_TEMPERATURE,
    MAX_TOKENS,
    ISSUE_CATEGORIES,
    NEW_ISSUE_CLASSIFICATION_THRESHOLD,
    MAX_STEPS_FIRST_RESPONSE,
    MAX_STEPS_FOLLOW_UP,
    RETURN_ALL_KB_STEPS,
    NEXT_STEPS_CHUNK_SIZE,
    KB_STEPS_RESPONSE_MODE,
)
from ..kb.loader import get_kb
from ..services.llm_client import OllamaClient
from ..services.classifier import get_classifier
from ..services.conversation_store import get_conversation_store
from ..services.request_queue import get_request_queue
from ..services.responder import ResponseGenerator
from ..services.escalation import EscalationClient, GraphEscalationClient
from ..utils.text_cleaner import clean_text
from ..config import (
    ESCALATION_WEBHOOK_URL,
    ESCALATION_WEBHOOK_API_KEY,
    ESCALATION_WEBHOOK_TIMEOUT_SECONDS,
    GRAPH_TENANT_ID,
    GRAPH_CLIENT_ID,
    GRAPH_CLIENT_SECRET,
    GRAPH_DELEGATE_USERNAME,
    GRAPH_DELEGATE_PASSWORD,
    GRAPH_SCOPES,
    IT_SUPPORT_CHAT_ID,
    IT_SUPPORT_TEAM_ID,
    IT_SUPPORT_CHANNEL_ID,
    GRAPH_TIMEOUT_SECONDS,
    TICKETING_ENABLED,
)
from ..tickets.assignment import create_and_assign_ticket

logger = logging.getLogger(__name__)


class ITSupportPipeline:
    """
    Main orchestration pipeline for IT support system
    
    Flow:
    1. Receive user input
    2. Clean text
    3. Classify issue category
    4. Lookup KB solution
    5. Generate response using LLM
    6. Return structured result
    """
    
    def __init__(self):
        """Initialize pipeline components"""
        logger.info("Initializing IT Support Pipeline...")
        
        # Initialize components
        self.kb = get_kb(KB_PATH)
        self.llm = OllamaClient(OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT)
        self.classifier = get_classifier()
        self.responder = ResponseGenerator(
            self.llm,
            max_steps_first_response=MAX_STEPS_FIRST_RESPONSE,
            max_steps_follow_up=MAX_STEPS_FOLLOW_UP,
        )
        self.conversations = get_conversation_store()
        self.escalation_webhook = EscalationClient(
            webhook_url=ESCALATION_WEBHOOK_URL,
            webhook_api_key=ESCALATION_WEBHOOK_API_KEY,
            timeout_seconds=ESCALATION_WEBHOOK_TIMEOUT_SECONDS,
        )
        self.escalation_graph = GraphEscalationClient(
            tenant_id=GRAPH_TENANT_ID,
            client_id=GRAPH_CLIENT_ID,
            client_secret=GRAPH_CLIENT_SECRET,
            username=GRAPH_DELEGATE_USERNAME,
            password=GRAPH_DELEGATE_PASSWORD,
            timeout_seconds=GRAPH_TIMEOUT_SECONDS,
            chat_id=IT_SUPPORT_CHAT_ID,
            team_id=IT_SUPPORT_TEAM_ID,
            channel_id=IT_SUPPORT_CHANNEL_ID,
            scopes=GRAPH_SCOPES,
        )
        # Tracks the last escalation prompt we asked per session so a "YES" can be
        # turned into a properly-scoped escalation message without guessing from history.
        self._pending_escalations: Dict[str, Dict[str, Any]] = {}
        # Tracks the last KB steps selected for a session so follow-ups like
        # "what is step 4" can be answered deterministically.
        self._last_kb_steps: Dict[str, Dict[str, Any]] = {}
        self._last_kb_sent_count: Dict[str, int] = {}
        
        logger.info("Pipeline initialized successfully")

    def _parse_step_request(self, text: str) -> Optional[int]:
        """Extract a 1-based step number from user follow-up like 'step 4' or 'fourth step'."""
        t = (text or "").strip().lower()
        if not t:
            return None

        word_to_num = {
            "one": 1,
            "two": 2,
            "three": 3,
            "four": 4,
            "five": 5,
            "six": 6,
            "seven": 7,
            "eight": 8,
            "nine": 9,
            "ten": 10,
            "first": 1,
            "second": 2,
            "third": 3,
            "fourth": 4,
            "fifth": 5,
            "sixth": 6,
            "seventh": 7,
            "eighth": 8,
            "ninth": 9,
            "tenth": 10,
        }

        m = re.search(r"\bstep\s*(\d{1,2})\b", t)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None

        m = re.search(r"\b(\d{1,2})(st|nd|rd|th)\b", t)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None

        for w, n in word_to_num.items():
            if re.search(rf"\b{re.escape(w)}\b", t) and ("step" in t):
                return n

        # "what is the fourth one" / "explain the fourth"
        for w, n in word_to_num.items():
            if re.search(rf"\b{re.escape(w)}\b", t) and re.search(r"\b(step|one)\b", t):
                return n

        return None

    def _is_next_steps_request(self, text: str) -> bool:
        tl = (text or "").strip().lower()
        if not tl:
            return False
        markers = [
            "what next",
            "what's next",
            "whats next",
            "next step",
            "next steps",
            "what should i do next",
            "ok next",
            "okay next",
            "still the same",
            "still same",
            "still not working",
            "same issue",
            "what now",
            "now what",
        ]
        return any(m in tl for m in markers)

    def _is_contact_it_support_request(self, text: str) -> bool:
        """Detect messages that are primarily asking to contact/escalate to IT Support."""
        tl = (text or "").strip().lower()
        if not tl:
            return False
        markers = [
            "contact it support",
            "contact the it support",
            "please contact it support",
            "please contact the it support",
            "escalate",
            "raise a ticket",
            "open a ticket",
            "create a ticket",
            "submit a ticket",
        ]
        return any(m in tl for m in markers)
    
    def check_health(self) -> Dict[str, bool]:
        """Check if all components are ready"""
        
        health = {
            "kb_loaded": len(self.kb.data) > 0,
            "ollama_available": self.llm.check_health(),
            "classifier_ready": self.classifier is not None
        }
        
        logger.info(f"Health check: {health}")
        return health
    
    def process(
        self,
        user_input: str,
        conversation_id: str = None,
        user_id: str = None,
        user_name: str = None,
    ) -> Dict:
        """
        Process user input through the pipeline
        
        Args:
            user_input: User's IT issue
            
        Returns:
            Dictionary with category, response, and metadata
        """
        
        process_started = time.perf_counter()
        llm_time_ms: Optional[float] = None

        def _success_result(**kwargs) -> Dict[str, Any]:
            total_time_ms = max(0.0, (time.perf_counter() - process_started) * 1000.0)
            base: Dict[str, Any] = {
                "status": "success",
                "total_time_ms": total_time_ms,
                "llm_time_ms": llm_time_ms,
                "model": getattr(self.llm, "model", None),
            }
            base.update(kwargs)
            return base

        def _error_result(error: str, response: str) -> Dict[str, Any]:
            total_time_ms = max(0.0, (time.perf_counter() - process_started) * 1000.0)
            return {
                "status": "error",
                "user_input": user_input,
                "error": error,
                "response": response,
                "total_time_ms": total_time_ms,
                "llm_time_ms": llm_time_ms,
                "model": getattr(self.llm, "model", None),
            }

        try:
            logger.info(f"Processing input: '{user_input[:50]}...'")

            # For Teams production: sessions are scoped by (conversation_id, user_id) and expire after inactivity.
            session = self.conversations.get_or_create(conversation_id or "default", user_id or "anonymous")
            session_id = session["session_id"]
            history = session["messages"]

            # Step 1: Clean text
            logger.debug("Step 1: Cleaning text...")
            cleaned_input = clean_text(user_input)
            logger.debug(f"  Cleaned: '{cleaned_input}'")

            prior_category = session.get("category") or self._infer_prior_category(history)
            has_prior = bool(prior_category and prior_category != "UNKNOWN")

            # If user is only asking to contact/escalate IT Support, don't try to re-classify a new issue.
            # Either apply it to the active issue (if we have one), or ask for the missing issue details.
            if self._is_contact_it_support_request(cleaned_input) and (session_id not in self._pending_escalations):
                if has_prior:
                    response = (
                        "Okay. If you want, I can escalate this to the IT Support group chat.\n\n"
                        "Reply YES to escalate this to the IT Support group chat.\n\n"
                        "If the issue persists, contact IT Support."
                    )
                    latest_issue = self._latest_issue_for_escalation(history, user_input)
                    self._pending_escalations[session_id] = {
                        "issue": latest_issue,
                        "user_input": user_input,
                        "latest_issue": latest_issue,
                        "user_name": user_name,
                    }
                    self.conversations.add_message(session_id, user_input, response)
                    return _success_result(
                        conversation_id=session["conversation_id"],
                        user_input=user_input,
                        category=prior_category,
                        response=response,
                        confidence=0.9,
                        is_follow_up=True,
                    )

                response = (
                    "I can help, but I need the issue details first. "
                    "What exactly is not working (app/device), and what is the exact error message?\n\n"
                    "If the issue persists, contact IT Support."
                )
                self.conversations.add_message(session_id, user_input, response)
                return _success_result(
                    conversation_id=session["conversation_id"],
                    user_input=user_input,
                    category="UNKNOWN",
                    response=response,
                    confidence=0.0,
                    is_follow_up=False,
                )

            # Escalation confirmation: if we previously asked to contact IT Support and user says YES,
            # create a tracked ticket (assigned to a technician) instead of just posting into a group chat.
            if (session_id in self._pending_escalations) and self._is_affirmative(cleaned_input):
                pending = self._pending_escalations.pop(session_id, None) or {}
                issue = pending.get("issue") or pending.get("user_input") or user_input
                response = self._escalate_to_ticket_or_webhook(
                    session=session,
                    issue=issue,
                    category=prior_category or "UNKNOWN",
                    user_id=user_id,
                    user_name=pending.get("user_name") or user_name,
                )

                self.conversations.add_message(session_id, user_input, response)
                return {
                    "status": "success",
                    "conversation_id": session["conversation_id"],
                    "user_input": user_input,
                    "category": prior_category or "UNKNOWN",
                    "response": response,
                    "confidence": 0.9,
                    "is_follow_up": True,
                }

            # LLM-based safety guardrail: requests that require admin/security access.
            if self._llm_requires_admin_action(cleaned_input):
                response = (
                    "I can’t perform admin or security actions (for example releasing a blocked/quarantined email, "
                    "unblocking a sender, resetting MFA/passwords, or granting access). "
                    "Please contact IT Support and include any error message, the affected account, and timestamps."
                )
                response = self._maybe_append_escalation_prompt(response, history)
                if "Reply YES to escalate this to the IT Support group chat." in response:
                    latest_issue = self._latest_issue_for_escalation(history, user_input)
                    self._pending_escalations[session_id] = {
                        "issue": latest_issue,
                        "user_input": user_input,
                        "latest_issue": latest_issue,
                        "user_name": user_name,
                    }
                self.conversations.add_message(session_id, user_input, response)
                return {
                    "status": "success",
                    "conversation_id": session["conversation_id"],
                    "user_input": user_input,
                    "category": "UNKNOWN",
                    "response": response,
                    "confidence": 0.0,
                    "is_follow_up": False,
                }
            
            # DB sessions intentionally store only message history; infer prior category
            # from the last user message when explicit category state is unavailable.
            # (already computed above for escalation/ack handling)

            # Handle pure acknowledgements without re-triggering troubleshooting.
            if has_prior and self._is_acknowledgement_only(cleaned_input):
                response = self._build_ack_response(prior_category)
                self.conversations.add_message(session_id, user_input, response)
                return {
                    "status": "success",
                    "conversation_id": session["conversation_id"],
                    "user_input": user_input,
                    "category": prior_category,
                    "response": response,
                    "confidence": 0.9,
                    "is_follow_up": True,
                }

            # General knowledge / trivia (after admin gate + acknowledgement handling).
            if self._should_refuse_non_it_question(cleaned_input):
                response = self._out_of_scope_refusal_response()
                self.conversations.add_message(session_id, user_input, response)
                return {
                    "status": "success",
                    "conversation_id": session["conversation_id"],
                    "user_input": user_input,
                    "category": "UNKNOWN",
                    "response": response,
                    "confidence": 0.0,
                    "is_follow_up": bool(has_prior),
                }
            
            # Step 2: Classify
            logger.debug("Step 2: Classifying issue...")
            category, confidence = self.classifier.classify(cleaned_input)
            
            # STRATEGY:
            # 1. First message in conversation → classify normally
            # 2. Follow-up (same issue) → keep prior category, let LLM answer dynamically
            # 3. New issue → only re-classify if confidence < 0.65
            
            explicit_new_issue = self._has_explicit_new_issue_marker(cleaned_input)
            is_follow_up = self._is_follow_up(cleaned_input, session, category, confidence)
            is_new_issue = self._is_new_issue(cleaned_input, prior_category)
            wants_guided = self._wants_guided_steps(cleaned_input)
            requested_step_num = self._parse_step_request(cleaned_input) if has_prior else None
            if requested_step_num is not None and session_id in self._last_kb_steps:
                last = self._last_kb_steps.get(session_id) or {}
                last_steps = last.get("steps") or []
                if 1 <= requested_step_num <= len(last_steps):
                    step_text = str(last_steps[requested_step_num - 1]).strip()
                    if not step_text:
                        step_text = "(step text unavailable)"

                    # Use LLM to explain the specific step, but stay grounded in the step text.
                    explanation = None
                    try:
                        if self.llm.check_health() and not RETURN_ALL_KB_STEPS:
                            prompt = f"""You are a first-tier IT Support assistant.

Explain ONLY the following troubleshooting step in simple, actionable terms.

Rules:
- Do not add extra steps.
- Do not add admin/security actions.
- If the user needs to contact IT Support, say so.
- Keep it to 3-6 short sentences or bullets.

Step {requested_step_num}: {step_text}
"""
                            explanation = self.llm.generate(prompt=prompt, temperature=0.2, max_tokens=180)
                    except Exception:
                        explanation = None

                    if explanation and explanation.strip():
                        response = f"Step {requested_step_num}: {step_text}\n\n{explanation.strip()}\n\nIf the issue persists, contact IT Support."
                    else:
                        response = f"Step {requested_step_num}: {step_text}\n\nIf the issue persists, contact IT Support."

                    self.conversations.add_message(session_id, user_input, response)
                    logger.info("Processing complete (step explanation)")
                    return _success_result(
                        conversation_id=session["conversation_id"],
                        user_input=user_input,
                        category=last.get("category") or prior_category or category,
                        response=response,
                        confidence=confidence,
                        is_follow_up=True,
                    )

            # Non-guided "what next" follow-up: continue through the same KB step list in chunks.
            if has_prior and is_follow_up and self._is_next_steps_request(cleaned_input) and session_id in self._last_kb_steps:
                last = self._last_kb_steps.get(session_id) or {}
                last_steps = list(last.get("steps") or [])
                if last_steps:
                    sent = int(self._last_kb_sent_count.get(session_id, 0))
                    chunk = max(1, int(NEXT_STEPS_CHUNK_SIZE))
                    start = max(0, min(sent, len(last_steps)))
                    end = min(len(last_steps), start + chunk)
                    next_steps = last_steps[start:end]
                    if next_steps:
                        numbered = "\n".join(f"{start + i + 1}. {s}" for i, s in enumerate(next_steps))
                        response = f"{numbered}\n\nIf the issue persists, contact IT Support."
                        self._last_kb_sent_count[session_id] = end
                    else:
                        response = (
                            "Those were the last steps in this playbook. If you're still blocked, "
                            "please contact IT Support with the exact error message, device, and timestamps.\n\n"
                            "If the issue persists, contact IT Support."
                        )

                    self.conversations.add_message(session_id, user_input, response)
                    logger.info("Processing complete (next steps chunk)")
                    return _success_result(
                        conversation_id=session["conversation_id"],
                        user_input=user_input,
                        category=last.get("category") or prior_category or category,
                        response=response,
                        confidence=confidence,
                        is_follow_up=True,
                    )
            
            # Handle low-confidence classification using LLM
            if category == "NEEDS_LLM_CLASSIFICATION":
                if has_prior and not is_new_issue:
                    # Low confidence but have prior conversation → stay on prior, let LLM handle
                    logger.info("Low confidence (%.2f) but has prior -> using prior category: %s", confidence, prior_category)
                    category = prior_category
                    confidence = 0.5
                else:
                    # New conversation or new issue → use LLM to classify
                    logger.info("Low confidence (%.2f) - using LLM to classify...", confidence)
                    category = self._llm_classify(cleaned_input, history)
                    confidence = 0.7
            
            # Prefer continuity: stay on prior issue unless user explicitly starts a new one
            # or classifier is very confident this is a different issue.
            if (
                has_prior
                and not explicit_new_issue
                and (
                    is_follow_up
                    or category == "NEEDS_LLM_CLASSIFICATION"
                    or confidence < NEW_ISSUE_CLASSIFICATION_THRESHOLD
                )
            ):
                logger.info("Continuing prior issue context: %s", prior_category)
                category = prior_category
                confidence = max(confidence, 0.6)
                is_follow_up = True
                is_new_issue = False

            # NEW ISSUE DETECTION: Only re-classify if confidence is still low for new issues
            if is_new_issue and has_prior and confidence < NEW_ISSUE_CLASSIFICATION_THRESHOLD:
                # User has prior conversation but is starting NEW issue with low confidence
                # Re-classify using LLM
                logger.info("New issue detected with low confidence (%.2f) -> re-classifying with LLM", confidence)
                category = self._llm_classify(cleaned_input, history)
                confidence = 0.7
                is_new_issue = True  # Confirmed new issue
            elif is_new_issue:
                # Fresh issue - use new classification
                logger.info(f"New issue detected: {category}")
                session["category"] = category
            elif wants_guided and prior_category and (is_follow_up and not is_new_issue and not explicit_new_issue):
                # Guided mode requested as a continuation of the *current* issue: keep prior category for KB context.
                logger.info(f"Guided continuation requested, using prior category: {prior_category}")
                category = prior_category
                confidence = 0.9
                is_follow_up = True
            elif is_follow_up and prior_category:
                # Follow-up to same issue → keep prior category, let LLM answer dynamically
                logger.info("Follow-up to prior issue: %s", prior_category)
                category = prior_category
                confidence = max(confidence, 0.6)

            # If still unknown after classification, ask LLM for an escalation-only response.
            if category == "UNKNOWN":
                # Ambiguous classifier; still distinguish random trivia vs real IT-but-unknown signals.
                if not self._looks_like_it_related(cleaned_input) and self._llm_confirms_pure_non_it(cleaned_input):
                    response = self._out_of_scope_refusal_response()
                else:
                    response = self._llm_escalation_response(cleaned_input)
                response = self._maybe_append_escalation_prompt(response, history)
                if "Reply YES to escalate this to the IT Support group chat." in response:
                    latest_issue = self._latest_issue_for_escalation(history, user_input)
                    self._pending_escalations[session_id] = {
                        "issue": latest_issue,
                        "user_input": user_input,
                        "latest_issue": latest_issue,
                        "user_name": user_name,
                    }
                self.conversations.add_message(session_id, user_input, response)
                return {
                    "status": "success",
                    "conversation_id": session["conversation_id"],
                    "user_input": user_input,
                    "category": "UNKNOWN",
                    "response": response,
                    "confidence": confidence,
                    "is_follow_up": False,
                }

            logger.debug(f"  Category: {category} (confidence: {confidence:.2f})")
            
            # Step 3: Lookup KB solution
            logger.debug("Step 3: Looking up KB solution...")
            solution, kb_confidence = self.kb.find_solution(cleaned_input, category)
            steps = self.kb.get_steps(category, cleaned_input)
            logger.debug(f"  Found KB solution (confidence: {kb_confidence:.2f})")

            # If we don't have approved steps for this category, treat it as UNKNOWN (escalation path).
            if not steps:
                category = "UNKNOWN"
                response = self._llm_escalation_response(cleaned_input)
                response = self._maybe_append_escalation_prompt(response, history)
                if "Reply YES to escalate this to the IT Support group chat." in response:
                    latest_issue = self._latest_issue_for_escalation(history, user_input)
                    self._pending_escalations[session_id] = {
                        "issue": latest_issue,
                        "user_input": user_input,
                        "latest_issue": latest_issue,
                        "user_name": user_name,
                    }
                self.conversations.add_message(session_id, user_input, response)
                return {
                    "status": "success",
                    "conversation_id": session["conversation_id"],
                    "user_input": user_input,
                    "category": "UNKNOWN",
                    "response": response,
                    "confidence": max(confidence, 0.3),
                    "is_follow_up": is_follow_up,
                }

            # One KB step per turn — user asks for guided steps or continues after Step N / next / failed / ok.
            if self._should_use_kb_guided_path(
                cleaned_input=cleaned_input,
                wants_guided_request=wants_guided,
                wants_all_now=self._wants_all_steps(cleaned_input),
                recent_history=history,
                category=category,
                steps=steps or [],
            ):
                last_shown = self._infer_last_kb_guided_step_number(history)
                if (
                    last_shown is not None
                    and last_shown >= len(steps)
                    and self._is_guided_plain_continuation_turn(cleaned_input)
                ):
                    response = (
                        "That was the last step in this playbook. If you're still blocked, "
                        "please contact IT Support for further assistance and include any "
                        "error text, device name, and when it started."
                    )
                else:
                    guided_idx = self._resolve_kb_guided_index(
                        cleaned_input,
                        wants_guided_request=wants_guided,
                        recent_history=history,
                    )
                    response = self._build_guided_step_response(steps, guided_idx)
                response = self._maybe_append_escalation_prompt(response, history)
                if "Reply YES to escalate this to the IT Support group chat." in response:
                    latest_issue = self._latest_issue_for_escalation(history, user_input)
                    self._pending_escalations[session_id] = {
                        "issue": latest_issue,
                        "user_input": user_input,
                        "latest_issue": latest_issue,
                        "user_name": user_name,
                    }
                self.conversations.add_message(session_id, user_input, response)
                logger.info("Processing complete")
                return {
                    "status": "success",
                    "conversation_id": session["conversation_id"],
                    "user_input": user_input,
                    "category": category,
                    "response": response,
                    "confidence": confidence,
                    "is_follow_up": is_follow_up,
                }

            # Cache applies only to non-follow-up responses.
            request_queue = get_request_queue()
            cached = None
            if not is_follow_up:
                cached = request_queue.get_cached(cleaned_input, category)
            if cached:
                response = cached
            else:
                if RETURN_ALL_KB_STEPS:
                    # Deterministic debug mode: return ALL approved KB steps (no LLM formatting).
                    if steps:
                        response = "\n".join(f"{i+1}. {step}" for i, step in enumerate(steps))
                        response = f"{response}\n\nIf the issue persists, contact IT Support."
                    else:
                        response = "Please contact IT Support."
                elif KB_STEPS_RESPONSE_MODE:
                    # Deterministic mode: return KB steps directly (no LLM formatting).
                    cap = MAX_STEPS_FOLLOW_UP if is_follow_up else MAX_STEPS_FIRST_RESPONSE
                    cap = max(1, int(cap))
                    chosen = (steps or [])[:cap]
                    if chosen:
                        response = "\n".join(f"{i+1}. {step}" for i, step in enumerate(chosen))
                        response = f"{response}\n\nIf the issue persists, contact IT Support."
                    else:
                        response = "Please contact IT Support."
                else:
                    # Step 4: Generate response using LLM (SINGLE LLM CALL)
                    logger.debug("Step 4: Generating response (1 LLM call)...")
                    response = self.responder.generate_response(
                        user_input=cleaned_input,
                        category=category,
                        solution=solution,
                        steps=steps,
                        conversation_history=history,
                        is_follow_up=is_follow_up,
                        temperature=LLM_TEMPERATURE,
                        max_tokens=MAX_TOKENS
                    )

                    if not response:
                        logger.warning("LLM response generation failed, using KB solution")
                        # Format KB solution as response
                        response = "IT Support Solution:\n\n" + "\n".join(f"{i+1}. {step}" for i, step in enumerate(steps)) + "\n\nIf the issue persists, contact IT Support."
                    else:
                        request_queue.set_cached(cleaned_input, category, response)

            # Persist the last KB steps for follow-up questions like "what is step 4".
            # Store even if the response was cached, since `steps` is computed per-request.
            if steps:
                self._last_kb_steps[session_id] = {
                    "category": category,
                    "steps": list(steps),
                    "updated_at": time.time(),
                }
                # Initialize / refresh how many steps we've "sent" in non-guided mode.
                if RETURN_ALL_KB_STEPS:
                    self._last_kb_sent_count[session_id] = len(steps)
                else:
                    # Best-effort: assume the initial answer covers up to MAX_STEPS_FIRST_RESPONSE steps.
                    # (Follow-ups can request "what next" to continue through the KB list deterministically.)
                    try:
                        cap = int(MAX_STEPS_FIRST_RESPONSE)
                    except Exception:
                        cap = 6
                    if not is_follow_up:
                        self._last_kb_sent_count[session_id] = min(len(steps), max(1, cap))

            # Persistent issue on follow-up: reinforce contacting IT Support (no automatic ticket posting).
            if (has_prior or is_follow_up) and self._is_failure_update(cleaned_input):
                suffix = (
                    "\n\nSince you're still blocked after troubleshooting, "
                    "please contact IT Support with screenshots or error text if you have them "
                    "(and when it started)."
                )
                if "contact it support" not in response.lower():
                    suffix = (
                        suffix.strip()
                        + "\n\nIf the issue persists, contact IT Support for further assistance."
                    )
                response = f"{response}{suffix}"

            response = self._maybe_append_escalation_prompt(response, history)
            if "Reply YES to escalate this to the IT Support group chat." in response:
                latest_issue = self._latest_issue_for_escalation(history, user_input)
                self._pending_escalations[session_id] = {
                    "issue": latest_issue,
                    "user_input": user_input,
                    "latest_issue": latest_issue,
                    "user_name": user_name,
                }
            self.conversations.add_message(session_id, user_input, response)

            # Step 5: Return result
            logger.info("Processing complete")
            
            return {
                "status": "success",
                "conversation_id": session["conversation_id"],
                "user_input": user_input,
                "category": category,
                "response": response,
                "confidence": confidence,
                "is_follow_up": is_follow_up,
            }
            
        except Exception as e:
            logger.error(f"Pipeline error: {e}", exc_info=True)
            return {
                "status": "error",
                "user_input": user_input,
                "error": str(e),
                "response": "Unable to process your request. Please contact IT Support."
            }

    def _is_follow_up(self, user_input: str, session: Dict, category: str, confidence: float) -> bool:
        """Detect short follow-up messages that depend on prior context."""
        # With the simplified session store we no longer persist a prior category.
        # Follow-up is inferred from message history + explicit continuation markers.
        if not session.get("messages"):
            return False

        text = user_input.lower()
        prior_category = session.get("category") or self._infer_prior_category(session.get("messages", []))

        # If classifier is reasonably confident this is a different issue category,
        # treat it as a new issue even if the message is short.
        if (
            prior_category
            and category not in {"UNKNOWN", "NEEDS_LLM_CLASSIFICATION"}
            and category != prior_category
            and confidence >= 0.70
        ):
            return False
        explicit_follow_up_markers = [
            "it still",
            "still",
            "same issue",
            "i tried",
            "tried",
            "step",
            "after",
            "what next",
            "next",
            "failed",
            "didn't work",
            "didnt work",
            "error",
            "crash",
            "crashed",
            "cable",
            "wire",
            "same",
        ]
        if any(marker in text for marker in explicit_follow_up_markers):
            return True

        # Short replies are usually follow-ups in an active session.
        if len(text.split()) <= 8:
            return True

        return False

    def _is_new_issue(self, user_input: str, prior_category: str) -> bool:
        """
        Detect if user is asking a NEW issue (not a follow-up).
        
        Returns True if this is a fresh issue, False if it's a follow-up to prior_category.
        """
        if not prior_category:
            return True  # No prior conversation = new issue
        
        text = user_input.lower()
        words = text.split()
        
        # GUIDED MODE REQUEST: User wants step-by-step - NOT a new issue!
        guided_markers = [
            "one step",
            "step by step",
            "first step",
            "next step",
            "give me one",
            "step in one",
            "if fail",
            "if fails",
            "then next",
            "let you know",
            "i'll perform",
            "i will perform",
            "try that",
            "didn't work",
            "didnt work",
            "failed",
            "still not",
            "still same",
        ]
        if any(marker in text for marker in guided_markers):
            return False  # This is a follow-up, not a new issue!
        
        # NEW ISSUE SIGNALS: User is starting fresh
        new_issue_markers = [
            "i have a new issue",
            "different issue",
            "new problem",
            "unrelated",
            "different problem",
            "another issue",
            "new issue",
            # Software installation specific
            "can't install",
            "cannot install",
            "unable to install",
            "installing",
            "installation",
            "download",
            "setup",
            # Explicit new starts
            "i want to",
            "i need to",
            "help me with",
            "can you help",
        ]
        
        # Check for new issue markers
        if any(marker in text for marker in new_issue_markers):
            return True
        
        # Default behavior: keep current issue context unless user clearly says
        # they are changing to another issue.
        return False

    def _has_explicit_new_issue_marker(self, user_input: str) -> bool:
        text = user_input.lower()
        markers = [
            "new issue",
            "different issue",
            "another issue",
            "separate issue",
            "unrelated",
            "switch topic",
        ]
        return any(marker in text for marker in markers)

    def _infer_prior_category(self, history: List[dict]) -> Optional[str]:
        """Infer prior category from latest user message in history."""
        if not history:
            return None

        latest_user = ""
        for item in reversed(history):
            candidate = (item.get("user") or "").strip()
            if candidate:
                latest_user = candidate
                break

        if not latest_user:
            return None

        category, confidence = self.classifier.classify(clean_text(latest_user))
        if category == "NEEDS_LLM_CLASSIFICATION" or confidence < 0.35:
            return None
        if category in {"UNKNOWN", "NEEDS_LLM_CLASSIFICATION"}:
            return None
        return category

    def _is_affirmative(self, text: str) -> bool:
        t = (text or "").strip().lower()
        return t in {
            "yes",
            "y",
            "yeah",
            "yep",
            "sure",
            "ok",
            "okay",
            "please",
            "do it",
            "contact",
            "contact them",
            "contact it",
            "contact support",
        }

    def _pending_escalation(self, history: List[Dict[str, Any]]) -> bool:
        if not history:
            return False
        last = history[-1] if isinstance(history[-1], dict) else {}
        assistant = (last.get("assistant") or "").lower()
        return ("should i contact it support" in assistant) or ("reply yes to escalate" in assistant)

    def _maybe_append_escalation_prompt(self, response: str, history: List[Dict[str, Any]]) -> str:
        if not response:
            return response
        tl = response.lower()
        if "contact it support" not in tl:
            return response
        # Avoid repeatedly appending the prompt.
        if ("should i contact it support" in tl) or ("reply yes to escalate" in tl):
            return response
        prompt = "Should I contact IT Support now? Reply YES to escalate this to the IT Support group chat."
        footer = "If the issue persists, contact IT Support."

        base = response.strip()
        if not base:
            return f"{prompt}\n\n{footer}"

        # Keep any extra text (some responses append additional "contact IT" guidance after the footer).
        # Make the last two blocks consistently:
        #   <existing content...>
        #   Should I contact...
        #   If the issue persists...
        #
        # Remove existing footer occurrences to avoid duplicates, then append canonical footer.
        base_no_footer = re.sub(rf"(?im)^\s*{re.escape(footer)}\s*$\n?", "", base).strip()
        if base_no_footer:
            return f"{base_no_footer}\n\n{prompt}\n\n{footer}"
        return f"{prompt}\n\n{footer}"

    def _issue_root_for_escalation(self, history: List[Dict[str, Any]], current_input: str) -> str:
        """Best-effort: capture the initial user message that started the current issue."""
        if not history:
            return current_input or ""

        # Walk backwards: if user explicitly started a new issue earlier, treat that as root.
        for item in reversed(history):
            prior = (item.get("user") or "").strip()
            if not prior:
                continue
            if self._has_explicit_new_issue_marker(clean_text(prior)):
                return prior

        # Otherwise use the oldest user message we still have in this session history.
        for item in history:
            prior = (item.get("user") or "").strip()
            if prior:
                return prior
        return current_input or ""

    def _latest_issue_for_escalation(self, history: List[Dict[str, Any]], current_input: str) -> str:
        """Capture the most recent *issue statement* (not 'next/failed/one-step' chatter)."""
        candidates: List[str] = []
        if current_input:
            candidates.append(current_input)
        for item in reversed(history or []):
            u = (item.get("user") or "").strip()
            if u:
                candidates.append(u)

        for text in candidates:
            cleaned = clean_text(text)
            tl = cleaned.lower().strip()
            if not tl:
                continue
            # Skip guided-mode meta-requests (these are not the underlying issue).
            if self._wants_guided_steps(tl):
                continue
            if "give me" in tl and "step" in tl:
                continue
            # Skip continuation/status-only replies.
            if self._is_guided_plain_continuation_turn(tl):
                continue
            if self._is_acknowledgement_only(tl):
                continue
            # Prefer a message that looks like an actual problem description.
            return text.strip()

        # Fallback to current input or oldest issue.
        return (current_input or "").strip() or self._issue_root_for_escalation(history, current_input)

    def _escalate_to_ticket_or_webhook(
        self,
        session: Dict[str, Any],
        issue: str,
        category: str,
        user_id: Optional[str],
        user_name: Optional[str],
    ) -> str:
        """Create a tracked ticket (preferred) or fall back to the old fire-and-forget
        webhook/Graph chat post if ticketing is disabled or ticket creation fails.
        """
        if TICKETING_ENABLED:
            try:
                result = create_and_assign_ticket(
                    conversation_id=session.get("conversation_id") or "",
                    user_id=user_id or "",
                    user_name=user_name,
                    category=category,
                    issue=issue,
                )
                return self._format_ticket_created_response(result)
            except Exception as exc:
                logger.error("Ticket creation failed, falling back to webhook: %s", exc, exc_info=True)

        escalation_payload = self._build_escalation_payload(user_name=user_name, issue=issue)
        result = (
            self.escalation_graph.send_to_support(escalation_payload)
            if self.escalation_graph.enabled()
            else self.escalation_webhook.send_webhook(escalation_payload)
        )
        if result.ok:
            return "Okay — I’ve sent this to IT Support. You should hear back soon."
        return (
            "I tried to contact IT Support but it failed. "
            f"Please contact IT Support directly. Error: {result.error}"
        )

    def _format_ticket_created_response(self, result: Dict[str, Any]) -> str:
        lines = [f"Your ticket (#{result['ticket_id']}) has been created."]
        technician = result.get("technician")
        if technician:
            lines[0] = f"Your ticket (#{result['ticket_id']}) has been assigned to {technician['name']}."
            if result.get("queue_position") is not None:
                lines.append(f"Current queue position: {result['queue_position']}")
            if result.get("eta_minutes") is not None:
                lines.append(f"Estimated response time: ~{result['eta_minutes']} minutes")
        else:
            lines.append("All technicians are currently busy; you're queued and will be assigned shortly.")
        lines.append(f"Priority: {result.get('priority', 'N/A')}")
        return "\n".join(lines)

    def _build_escalation_payload(self, user_name: Optional[str], issue: str) -> Dict[str, Any]:
        # Build a concise message for IT Support:
        # - who (display name)
        # - what issue (captured at the time we asked to escalate)
        display = (user_name or "").strip() or "Unknown user"
        issue = (issue or "").strip()
        issue_line = issue or "(not provided)"
        # Prefer a vertical, readable layout (Teams renders newlines cleanly).
        text = "\n".join(
            [
                "IT Support escalation",
                f"User: {display}",
                f"Issue: {issue_line}"
                
            ]
        )
        # Some Graph endpoints support HTML bodies; if unsupported it will fall back to plain text.
        html = (
            "<div>"
            "<span style=\"color:#c50f1f;\"><b>IT Support escalation</b></span><br/>"
            f"<b>User:</b> {display}<br/>"
            f"<b>Issue:</b> {issue_line}<br/>"
            "<b>Escalated by:</b> IT Support Bot"
            "</div>"
        )
        return {
            "user_name": display,
            "issue": issue,
            "text": text,
            "html": html,
            "context": {},
        }

    def _is_acknowledgement_only(self, user_input: str) -> bool:
        text = user_input.lower().strip()
        if not text:
            return False

        markers = [
            "thanks",
            "thank you",
            "got it",
            "okay thanks",
            "ok thanks",
            "noted",
            "cool thanks",
            "great thanks",
        ]
        if not any(marker in text for marker in markers):
            return False

        escalation_or_continue = [
            "not working",
            "still",
            "failed",
            "error",
            "next step",
            "what next",
            "how to fix",
            "issue",
            "problem",
        ]
        if any(marker in text for marker in escalation_or_continue):
            return False

        return len(text.split()) <= 8

    def _build_ack_response(self, category: str) -> str:
        readable = category.lower().replace("_", " ").replace(" issue", "")
        return (
            f"You're welcome. I am here if you want to continue troubleshooting this {readable} issue.\n\n"
            "If the issue persists, contact IT Support."
        )

    def _out_of_scope_refusal_response(self) -> str:
        return (
            "I'm IT Support Bot — I only help with workplace IT issues "
            "(computers, devices, Teams, Outlook, email, printers, headsets, installs, crashes, MFA/password "
            "self-service tips, VPN/Wi‑Fi/network problems, etc.). "
            "I can't answer general trivia or unrelated topics.\n\n"
            "Describe briefly what workplace tech issue you're hitting and we'll troubleshoot.\n\n"
            "Please contact IT Support."
        )

    def _looks_like_it_related(self, text: str) -> bool:
        tl = text.lower()
        needles = [
            # symptoms / meta
            "issue", "problem", "error", "broken", "crash", "not working", "doesn't work", "does not work",
            "cant ", "can't", "won't", "unable", "help", "fix", "troubleshoot", "support",
            # areas
            "password", "mfa", "2fa", "vpn", "wifi", "wi-fi", "internet", "network", "printer", "outlook",
            "teams", "onedrive", "sharepoint", "excel", "word", "laptop", "desktop", "pc", "monitor", "dock",
            "usb", "bluetooth", "headset", "mic", "audio", "camera", "display", "kb", "mouse", "keyboard",
            "install", "uninstall", "update", "driver", "admin", "quarantine", "blocked", "phish", "phishing",
            "login", "sign in", "session", "lock", "email", "inbox", "calendar", "meeting",
        ]
        return any(n in tl for n in needles)

    def _obvious_general_knowledge_stem(self, tl: str) -> bool:
        stems = (
            "who is ",
            "who was ",
            "who were ",
            "who are ",
            "tell me who ",
            "what is the capital",
            "capital of ",
            "net worth ",
            "trivia quiz",
            "celebrity ",
            "celebrities",
            "how many olymp",
            "super bowl champ",
            "who won ",
            "explain quantum physic",
            "meaning of life",
        )
        return any(s in tl for s in stems)

    def _llm_confirms_pure_non_it(self, user_input: str) -> bool:
        """True when unrelated to workplace IT troubleshooting (allows refusing trivia / small talk)."""
        try:
            if not user_input.strip() or self._looks_like_it_related(user_input):
                return False
            if not self.llm.check_health():
                return False
            prompt = f"""Reply with ONLY YES or NO.

YES = the user message has NOTHING to do with workplace IT/device/software/network/email/Teams/password/MFA/hardware/printer/install/troubleshooting.
NO = it could reasonably be an IT/helpdesk question (even if vague).

User message:
"{user_input}"
"""
            verdict = (self.llm.generate(prompt=prompt, temperature=0.0, max_tokens=6) or "").strip().upper()
            return verdict.startswith("YES") or verdict == "Y"
        except Exception:
            return False

    def _should_refuse_non_it_question(self, text: str) -> bool:
        t = (text or "").strip()
        if not t or self._looks_like_it_related(t):
            return False
        tokens = t.lower().split()
        if len(tokens) <= 4 and any(
            w in tokens
            for w in (
                "thanks",
                "thank",
                "ok",
                "okay",
                "yes",
                "no",
                "yep",
                "nope",
                "cool",
                "great",
            )
        ):
            return False
        tl_full = t.lower()
        # Gratitude / short chat — never mis-classify as "out of scope trivia".
        if (
            ("thank you" in tl_full or tl_full.startswith("thanks") or " thanks" in tl_full)
            and len(tokens) <= 16
            and ("issue" not in tl_full and "not working" not in tl_full and "error" not in tl_full)
        ):
            return False
        if self._obvious_general_knowledge_stem(tl_full):
            return True
        return self._llm_confirms_pure_non_it(t)

    def _infer_last_kb_guided_step_number(self, history: List[dict]) -> Optional[int]:
        """1-based ''Step N'' from the most recent assistant message that contained it."""
        for entry in reversed(history or []):
            blob = entry.get("assistant") or ""
            m = re.search(r"step\s+(\d+)\s*:", blob[:8000], re.IGNORECASE)
            if m:
                try:
                    return max(1, int(m.group(1)))
                except ValueError:
                    return None
        return None

    def _guided_restart_requested(self, user_input: str) -> bool:
        t = user_input.lower()
        if self._wants_guided_steps(user_input) and (
            "from the beginning" in t or "from start" in t or "from scratch" in t
        ):
            return True
        return ("restart" in t.split() or "again" in t.split() or "redo" in t) and (
            "step by step" in t or "one by one" in t or "one step" in t or "guided" in t
        )

    def _is_guided_plain_continuation_turn(self, user_input: str) -> bool:
        """Advance after Step N — short confirmations / next / fail (not fresh issue descriptions)."""
        t_raw = user_input.strip()
        tl = t_raw.lower()
        if len(tl) > 100:
            return False
        if self._wants_guided_steps(user_input):
            # New guided request without prior Step bubbles should start at step 1, not continuation.
            if "step by step" in tl or "one by one" in tl or "one step at a time" in tl:
                return False
            if tl.strip() in {"next step", "next"}:
                return True
        markers = [
            "next", "failed", "didn't work", "didnt work", "still not", "still no",
            "okay", "ok", "yes", "yep", "nope",
            "done", "did it", "tried",
            "worked", "that worked",
            "same issue", "same problem",
            "continue", "proceed",
        ]
        if any(m in tl for m in markers):
            return True
        # Do not treat short new-issue descriptions as continuation. Continuation must be explicit
        # (next/failed/ok/etc.) so we don't jump to a later step on a fresh issue.
        return False

    def _should_use_kb_guided_path(
        self,
        cleaned_input: str,
        wants_guided_request: bool,
        wants_all_now: bool,
        recent_history: List[dict],
        category: str,
        steps: List[str],
    ) -> bool:
        if wants_all_now or category in {"UNKNOWN", "NEEDS_LLM_CLASSIFICATION"}:
            return False
        if not steps:
            return False
        last_n = self._infer_last_kb_guided_step_number(recent_history)
        if wants_guided_request:
            return True
        if last_n is not None and self._is_guided_plain_continuation_turn(cleaned_input):
            return True
        return False

    def _resolve_kb_guided_index(self, cleaned_input: str, wants_guided_request: bool, recent_history: List[dict]) -> int:
        """Map to 0-based KB step index used by _build_guided_step_response (shows Step index+1)."""
        last_n = self._infer_last_kb_guided_step_number(recent_history)
        tl = cleaned_input.lower()

        if last_n is None:
            return 0

        if wants_guided_request and self._guided_restart_requested(cleaned_input):
            return 0

        if wants_guided_request:
            # If the user is explicitly continuing ("next"/"failed"/"ok"), advance.
            # Otherwise, treat this as a (possibly new) guided request and restart at Step 1.
            if self._is_guided_plain_continuation_turn(cleaned_input):
                return last_n
            return 0

        # Plain continuation after assistant showed Step last_n ("next"/"failed"/short reply).
        return last_n

    def _wants_guided_steps(self, user_input: str) -> bool:
        text = user_input.lower()
        markers = [
            "step by step",
            "one by one",
            "first step",
            "one step at a time",
            "give me one step",
            # avoid bare "next step" / "first step" alone as mode switch — resolver handles advancement
            "not all the steps",
            "don't give me all",
            "dont give me all",
            "not all steps",
            "one step only",
            "only one step",
            "slowly guide",
            "guide me slowly",
            "one step",
            "if fail",
            "if fails",
            "then second",
            "first first",
            "try it step",
            "first solution",
            "step first",
            "one stp",
            "1 step",
            "first stp",
            "slow step",
        ]
        return any(marker in text for marker in markers)

    def _wants_all_steps(self, user_input: str) -> bool:
        """Detect when user wants ALL steps at once (exits guided mode).
        
        Only match if user explicitly says they DON'T want step-by-step.
        """
        text = user_input.lower()
        
        # Strong signals - user explicitly wants all at once
        explicit_all = [
            "give me all the steps",
            "list them all",
            "just give me everything",
            "don't guide me",
            "not step by step",
            "skip the step by step",
        ]
        if any(marker in text for marker in explicit_all):
            return True
        
        # Only exit guided mode if NOT also asking for guided/step-by-step
        wants_guided = self._wants_guided_steps(user_input)
        wants_solution_only = "give me the solution" in text or "just the solution" in text
        
        # Exit only if wants solution BUT NOT guided
        return wants_solution_only and not wants_guided

    def _is_failure_update(self, user_input: str) -> bool:
        text = user_input.lower()
        markers = [
            "fail",
            "failed",
            "not working",
            "still",
            "same issue",
            "same isuue",
            "same isssue",
            "till same",
            "still same",
            "still not",
            "didn't work",
            "didnt work",
            "not worked",
            "crash",
            "crashed",
            "error",
            "next",
            "second",
        ]
        return any(marker in text for marker in markers)

    def _is_success_update(self, user_input: str) -> bool:
        text = user_input.lower()
        markers = [
            "worked",
            "fixed",
            "resolved",
            "done",
            "it works",
            "working now",
            "thank",
        ]
        return any(marker in text for marker in markers)

    def _build_guided_step_response(self, steps, step_index: int) -> str:
        if not steps:
            return "I do not have a troubleshooting step for this yet. Please contact IT Support."

        step_index = max(0, min(step_index, len(steps) - 1))
        step = steps[step_index]
        next_hint = "Try this first and tell me what happened. If it fails, say 'failed' and I will give you the next step."

        if step_index == len(steps) - 1:
            next_hint = "This is the last listed step. If it still fails after this, contact IT Support."

        return f"Step {step_index + 1}: {step}\n\n{next_hint}"

    def _llm_classify(self, user_input: str, history: list) -> str:
        """
        Use LLM to classify when rule-based classifier has low confidence.
        
        Args:
            user_input: The user's input text
            history: Conversation history for context
            
        Returns:
            Category string from ISSUE_CATEGORIES
        """
        # Only allow KB-backed categories here. If the request doesn't clearly fit, return UNKNOWN.
        categories = [
            "HEADSET_ISSUE",
            "DISPLAY_ISSUE",
            "KEYBOARD_MOUSE_ISSUE",
            "NETWORK_ISSUE",
            "TEAMS_ISSUE",
            "OUTLOOK_ISSUE",
            "HARDWARE_ISSUE",
            "SOFTWARE_INSTALLATION",
            "UNKNOWN",
        ]
        categories_str = ", ".join(categories)
        
        prompt = f"""Classify this IT support request into exactly ONE category from this list:
 {categories_str}

User request: "{user_input}"

Respond with ONLY the category name, nothing else. Example response: HEADSET_ISSUE"""

        try:
            response = self.llm.generate(prompt, temperature=0.1, max_tokens=30)
            if response:
                response = response.strip()
                for cat in categories:
                    if response.strip().upper() == cat:
                        logger.info("LLM classified as: %s", cat)
                        return cat
        except Exception as e:
            logger.error("LLM classification error: %s", e)

        return "UNKNOWN"

    def _keyword_fallback_classify(self, user_input: str) -> str:
        """Fallback classification using simple keyword matching."""
        text = user_input.lower()
        
        # Priority keywords (more specific first)
        keyword_map = {
            "OUTLOOK_ISSUE": ["outlook", "email", "mail", "calendar", "inbox"],
            "TEAMS_ISSUE": ["teams", "meeting", "video call"],
            "NETWORK_ISSUE": ["wifi", "internet", "network", "connection"],
            "HEADSET_ISSUE": ["headset", "headphone", "audio", "mic", "speaker"],
            "DISPLAY_ISSUE": ["screen", "display", "monitor", "black"],
            "KEYBOARD_MOUSE_ISSUE": ["keyboard", "mouse", "trackpad"],
            "HARDWARE_ISSUE": ["printer", "usb", "drive"],
            "SOFTWARE_INSTALLATION": ["install", "download", "software"],
        }
        
        for category, keywords in keyword_map.items():
            for kw in keywords:
                if kw in text:
                    logger.info(f"Keyword fallback classified as: {category}")
                    return category
        
        return "UNKNOWN"

    def _llm_requires_admin_action(self, user_input: str) -> bool:
        """Ask the LLM if the request requires admin/security access.

        This avoids keyword hardcoding so phrasing and typos still work.
        If Ollama is unavailable, return False so the bot can still operate in
        KB-fallback mode (with no LLM gate).
        """
        try:
            # Never gate simple confirmations/acknowledgements.
            if user_input.strip().lower() in {"yes", "y", "ok", "okay", "no", "n"}:
                return False
            if not self.llm.check_health():
                return False

            prompt = f"""You are a strict IT support triage classifier.

Task: Determine if the user's request requires admin/security access that an end-user troubleshooting bot cannot perform.

Examples that REQUIRE admin/security access:
- releasing a blocked/quarantined email
- unblocking/whitelisting a sender or domain
- resetting MFA/2FA or passwords
- unlocking accounts
- granting access/permissions or adding users to groups

If the request requires admin/security access, respond with ONLY: YES
Otherwise respond with ONLY: NO

User request: "{user_input}"
"""

            verdict = self.llm.generate(prompt=prompt, temperature=0.0, max_tokens=5)
            if not verdict:
                return False
            verdict = verdict.strip().upper()
            return verdict.startswith("YES")
        except Exception:
            return False

    def _llm_escalation_response(self, user_input: str) -> str:
        """Generate a short escalation message for out-of-scope or unknown issues."""
        fallback = (
            "I can’t help with this request here. Please contact IT Support and include any error message, "
            "the affected account/system, and timestamps."
        )
        try:
            if not self.llm.check_health():
                return fallback

            prompt = f"""You are a first-tier IT support assistant.

The user request is not in the supported self-service troubleshooting categories, or it requires IT/admin action.

Write a short response that:
- clearly says to contact IT Support
- asks for 3 key details (error message, affected account/system, timestamps)
- does not provide troubleshooting steps

User request: "{user_input}"
"""
            response = self.llm.generate(prompt=prompt, temperature=0.1, max_tokens=80)
            if response and response.strip():
                text = response.strip()
                if "contact it support" not in text.lower():
                    text += "\n\nPlease contact IT Support."
                return text
        except Exception:
            return fallback

        return fallback


# Global pipeline instance
_pipeline = None


def get_pipeline() -> ITSupportPipeline:
    """Get or create pipeline instance"""
    global _pipeline
    if _pipeline is None:
        _pipeline = ITSupportPipeline()
    return _pipeline
