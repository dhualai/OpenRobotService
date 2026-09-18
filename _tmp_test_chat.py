import sys, os, asyncio
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "ai", ".env"))

async def main():
    from ai.agents.AiDataAnalysisPlatform.agent import DataAnalysisAgent
    agent = DataAnalysisAgent.from_env()
    try:
        resp = await agent.chat(question="工单解决率")
        print("OK:", resp.mode)
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())