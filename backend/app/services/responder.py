"""
Response generator using LLM and KB.
"""

import logging
import re
from typing import List, Optional

logger = logging.getLogger(__name__)

FOOTER_LINE = "If the issue persists, contact IT Support."


class ResponseGenerator:
    """Generate IT support responses using LLM and KB"""
    
    def __init__(self, llm_client, max_steps_first_response: int = 6, max_steps_follow_up: int = 3):
        """
        Initialize response generator
        
        Args:
            llm_client: OllamaClient instance
        """
        self.llm = llm_client
        self.max_steps_first_response = max(1, int(max_steps_first_response))
        self.max_steps_follow_up = max(1, int(max_steps_follow_up))
    
    def generate_response(
        self,
        user_input: str,
        category: str,
        solution: str,
        steps: Optional[List[str]] = None,
        conversation_history: Optional[List[dict]] = None,
        is_follow_up: bool = False,
        temperature: float = 0.3,
        max_tokens: int = 500
    ) -> Optional[str]:
        """
        Generate formatted IT support response
        
        Args:
            user_input: Original user issue
            category: Detected category
            solution: KB solution
            temperature: LLM temperature
            max_tokens: Max response length
            
        Returns:
            Formatted response or None if error
        """
        
        # Build prompt
        prompt = self._build_prompt(
            user_input=user_input,
            category=category,
            solution=solution,
            steps=steps or [],
            conversation_history=conversation_history or [],
            is_follow_up=is_follow_up,
        )
        
        logger.debug(f"Prompt:\n{prompt}")
        
        # Call LLM
        response = self.llm.generate(
            prompt=prompt,
            temperature=temperature,
            max_tokens=max_tokens
        )
        
        # Always use LLM response if we got anything
        if response and len(response.strip()) > 0:
            logger.info("Response generated successfully")
            formatted_response = self._format_response(response, is_follow_up=is_follow_up)
            return formatted_response
        else:
            logger.error("LLM returned empty response")
            return self._fallback_response(steps or [], is_follow_up)
    
    def _build_prompt(
        self,
        user_input: str,
        category: str,
        solution: str,
        steps: List[str],
        conversation_history: List[dict],
        is_follow_up: bool,
    ) -> str:
        """
        Build prompt for LLM
        
        Uses exact prompt template specified in requirements
        """
        
        history_text = self._format_history(conversation_history)

        steps_text = "\n".join(f"- {step}" for step in steps) if steps else "(No approved steps available.)"
        first_min = 3
        first_max = self.max_steps_first_response
        follow_max = self.max_steps_follow_up

        prompt = f"""You are a first-tier IT Support assistant.

Hard rules:
- Only help with standard end-user troubleshooting.
- If the request requires admin/security actions (for example releasing a blocked/quarantined email, unblocking a sender, resetting MFA/passwords, or granting access/permissions), do NOT provide steps. Reply: "Please contact IT Support." and ask for key details (error message, affected account, timestamps).
- Do not invent steps. Only use the approved steps provided below.
- If none of the approved steps apply, say: "Please contact IT Support."

Conversation context:
{history_text}

Detected issue category: {category}
Is this a follow-up turn: {is_follow_up}

User problem: {user_input}

Approved steps from knowledge base (pick only what matches the user's specific issue):
{steps_text}

Instructions:
- Keep continuity with the existing issue context unless user clearly asked to switch to a new issue.
- Select ONLY relevant steps from the approved list (do not include irrelevant steps).
- Use a clean numbered list format (each step on its own line), no big paragraphs.
- For first response on an issue: provide {first_min}-{first_max} concise steps.
- For follow-up responses: focus on next 1-{follow_max} best actions based on what user just said.
- If you need more info, ask 1-2 clarifying questions instead of listing generic steps.
- End with: If the issue persists, contact IT Support.

Write the final response now."""
         
        return prompt

    def _format_history(self, conversation_history: List[dict]) -> str:
        if not conversation_history:
            return "No prior messages."

        recent = conversation_history[-4:]
        lines = []
        for item in recent:
            lines.append(f"User: {item.get('user', '')}")
            assistant = item.get("assistant", "").replace("\n", " ")
            lines.append(f"Assistant: {assistant[:300]}")
        return "\n".join(lines)
    
    def _format_response(self, raw_response: str, is_follow_up: bool) -> str:
        """Format LLM response for presentation"""

        response = (raw_response or "").strip()
        max_steps = self.max_steps_follow_up if is_follow_up else self.max_steps_first_response
        response = self._normalize_to_numbered_steps(response, max_steps=max_steps)
        response = self._ensure_footer_last(response)
        return response

    def _ensure_footer_last(self, text: str) -> str:
        """Ensure the standard footer exists and is the final line."""
        t = (text or "").strip()
        if not t:
            return FOOTER_LINE

        # Remove any existing footer variants anywhere, then append canonical footer.
        lines = [ln.rstrip() for ln in t.splitlines()]
        filtered: List[str] = []
        for ln in lines:
            if re.search(r"\bcontact\s+it\s+support\b", (ln or ""), flags=re.IGNORECASE) and "issue persists" in (
                (ln or "").lower()
            ):
                continue
            if (ln or "").strip().lower() == "please contact it support.":
                continue
            filtered.append(ln)

        cleaned = "\n".join(filtered).strip()
        if cleaned:
            return f"{cleaned}\n\n{FOOTER_LINE}"
        return FOOTER_LINE

    def _normalize_to_numbered_steps(self, text: str, max_steps: int) -> str:
        """Convert common LLM paragraph/bullets into a clean numbered list."""
        t = (text or "").strip()
        if not t:
            return t

        # Strip leading headings and filler intros.
        t = re.sub(r"^\s*(it support solution:|solution:|try these first:)\s*", "", t, flags=re.IGNORECASE)

        raw_lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
        steps: List[str] = []

        # Prefer explicit bullet/numbered lines.
        for ln in raw_lines:
            m = re.match(r"^(\d+)[\)\.\-]\s+(.*)$", ln)
            if m:
                steps.append(m.group(2).strip())
                continue
            m = re.match(r"^[-•\*]\s+(.*)$", ln)
            if m:
                steps.append(m.group(1).strip())
                continue

        if not steps:
            # Fallback: split into short actionable sentences.
            flattened = re.sub(r"\s+", " ", t)
            parts = re.split(r"(?<=[\.\?\!])\s+(?=[A-Z0-9])", flattened)
            for p in parts:
                p = p.strip()
                if not p:
                    continue
                if re.search(r"\bcontact\s+it\s+support\b", p, flags=re.IGNORECASE):
                    continue
                steps.append(p.rstrip("."))

        # Cap to keep readability; follow-up responses should be short anyway.
        steps = [s for s in (s.strip() for s in steps) if s]
        limit = max(1, int(max_steps))
        if len(steps) > limit:
            steps = steps[:limit]

        numbered = "\n".join(f"{idx + 1}. {step}" for idx, step in enumerate(steps))
        return numbered or t

    def _format_solution(self, solution: str) -> str:
        """Format KB solution as fallback"""
        
        formatted = f"IT Support Solution:\n\n{solution}\n\n{FOOTER_LINE}"
        
        return formatted

    def _fallback_response(self, steps: List[str], is_follow_up: bool) -> str:
        """Graceful fallback when LLM is unavailable/timed out."""
        if is_follow_up:
            return (
                "I could not generate a dynamic follow-up right now. "
                "Since you already tried the guided steps and the issue still persists, "
                "please contact IT Support with the exact error message, affected account/device, and timestamp.\n\n"
                f"{FOOTER_LINE}"
            )

        top_steps = steps[:4]
        if not top_steps:
            return "Please contact IT Support."

        numbered = "\n".join(f"{idx + 1}. {step}" for idx, step in enumerate(top_steps))
        return (
            "Try these first:\n\n"
            f"{numbered}\n\n"
            f"{FOOTER_LINE}"
        )

    def _is_useful_response(self, response: str) -> bool:
        """Accept any non-empty response from LLM."""
        normalized = response.strip().lower()
        
        # Reject only truly empty or useless responses
        if len(normalized) < 30:
            return False
        
        # Only reject pure escalation with no content
        if normalized in {
            "if the issue persists, contact it support.",
            "contact it support.",
        }:
            return False
        
        # Accept any response with actual content
        return True


class PromptBuilder:
    """Build prompts for different use cases"""
    
    @staticmethod
    def build_classification_prompt(user_input: str) -> str:
        """Build prompt for category classification (if using LLM for classification)"""
        
        categories = [
            "Email", "System", "Network", "Access",
            "Application", "Teams", "URL", "Software"
        ]
        
        prompt = f"""You are an IT Support categorizer.

User Issue: {user_input}

Possible categories: {', '.join(categories)}

Respond with ONLY the category name that best matches the issue."""
        
        return prompt
    
    @staticmethod
    def build_response_prompt(
        user_input: str,
        category: str,
        solution: str
    ) -> str:
        """Build the main response generation prompt"""
        
        return f"""You are a first-tier IT Support assistant.

User Issue:
{user_input}

Detected Category:
{category}

Knowledge Base Solution:
{solution}

Rules:
- Give step-by-step response
- Be clear and concise
- Do not add extra assumptions
- If solution exists, strictly follow it
- Include the actual numbered steps from the Knowledge Base Solution
- Do not mention internal category names
- End with: If the issue persists, contact IT Support.

Return final response only."""
