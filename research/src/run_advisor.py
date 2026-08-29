"""
Project Atlas LLM Advisor — Interactive Chat CLI
=================================================
Run this to talk to the Atlas Advisor in plain English.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm.atlas_advisor import AtlasAdvisor


BANNER = """
╔══════════════════════════════════════════════════════════════════════╗
║          PROJECT ATLAS — AI Investment Advisor                       ║
║          Powered by Local Ollama & Groq Cloud                        ║
║                                                                      ║
║  Ask anything in plain English, for example:                         ║
║  • "I have ₹50,000 to invest. What should I buy?"                   ║
║  • "I want monthly dividend income. I can invest ₹8,000/month."     ║
║  • "Compare Coal India and ONGC for me."                             ║
║  • "Tell me everything about ITC stock."                             ║
║  • "If I invest ₹10,000/month for 3 years, what will I have?"       ║
║                                                                      ║
║  Commands: 'reset' (new chat) | 'quit' (exit)                       ║
╚══════════════════════════════════════════════════════════════════════╝
"""

DISCLAIMER = """
⚠️  DISCLAIMER: Atlas Advisor is a research tool, not a SEBI-registered
   investment advisor. All suggestions are based on quantitative models.
   Please consult a certified financial advisor before investing.
"""


def main():
    print(BANNER)
    print(DISCLAIMER)

    # Initialize the advisor
    try:
        advisor = AtlasAdvisor()
        print(f"  ✅ Atlas Advisor ready\n")
    except RuntimeError as e:
        print(f"\n  ❌ Setup Error:\n  {e}\n")
        sys.exit(1)

    # Chat loop
    while True:
        try:
            user_input = input("\n  You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n  Goodbye! Happy investing! 🚀\n")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q"):
            print("\n  Goodbye! Happy investing! 🚀\n")
            break

        if user_input.lower() == "reset":
            advisor.reset()
            print("  Conversation reset. Starting fresh!\n")
            continue

        # Get response
        try:
            print()
            response = advisor.chat(user_input)
            print(f"\n  Atlas: {response}\n")
        except Exception as e:
            print(f"\n  ❌ Error: {e}")
            print("  If Ollama crashed, restart it and try again.\n")


if __name__ == "__main__":
    main()
