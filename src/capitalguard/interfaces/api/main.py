# --- START OF FILE: src/capitalguard/interfaces/api/main.py ---
from fastapi import FastAPI, HTTPException, Depends, Request
from capitalguard.boot import build_services
from capitalguard.interfaces.api.deps import require_api_key
from capitalguard.interfaces.api.schemas import RecommendationOut, CloseIn

def build_api_app() -> FastAPI:
    app = FastAPI(title="CapitalGuard API")

    # حقن نفس الخدمات في app.state
    services = build_services()
    app.state.services = services

    @app.get("/recommendations",
             response_model=list[RecommendationOut],
             dependencies=[Depends(require_api_key)])
    def list_recs(request: Request, channel_id: int | None = None):
        trade = request.app.state.services["trade_service"]
        items = trade.list_all(channel_id)
        return [RecommendationOut.model_validate(i) for i in items]

    @app.post("/recommendations/{rec_id}/close",
              response_model=RecommendationOut,
              dependencies=[Depends(require_api_key)])
    def close_rec(request: Request, rec_id: int, payload: CloseIn):
        trade = request.app.state.services["trade_service"]
        try:
            rec = trade.close(rec_id, payload.exit_price)
            return RecommendationOut.model_validate(rec)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))

    return app

app = build_api_app()
# --- END OF FILE ---