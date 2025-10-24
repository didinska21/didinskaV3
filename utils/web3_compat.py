# utils/web3_compat.py
try:
    # Web3 v7+
    from web3.middleware import ExtraDataToPOAMiddleware as POA_MIDDLEWARE
except ImportError:
    # Web3 v6
    from web3.middleware import geth_poa_middleware as POA_MIDDLEWARE

def inject_poa(w3):
    """Inject POA middleware di layer 0 (aman untuk v6/v7)."""
    try:
        w3.middleware_onion.inject(POA_MIDDLEWARE, layer=0)
    except Exception:
        pass
