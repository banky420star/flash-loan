from __future__ import annotations

import time

from .rpc import Rpc, RpcError


_RATE_LIMIT_MARKERS = ('429', 'too many requests', 'rate limit', 'rate-limit')


def _failover_eligible(exc: RpcError) -> bool:
    text = str(exc).lower()
    if any(marker in text for marker in _RATE_LIMIT_MARKERS):
        return True
    return not text.startswith('rpc error:')


class RpcPool:
    """Ordered RPC failover with cooldown for transport/rate-limit failures."""

    def __init__(self, urls, *, client_factory=Rpc, cooldown_s: float = 5.0,
                 clock=None):
        endpoints=[]
        for raw in urls:
            value=str(raw).strip()
            if value and value not in endpoints:
                endpoints.append(value)
        if not endpoints:
            raise ValueError('at least one RPC endpoint is required')
        self.endpoints=tuple(endpoints)
        self.clients={url:client_factory(url) for url in self.endpoints}
        self.cooldown_s=max(0.0,float(cooldown_s))
        self._clock=clock or time.monotonic
        self._health={url:{'failures':0,'successes':0,'cooldown_until':0.0,
                           'last_error':None} for url in self.endpoints}
        self.current_endpoint=self.endpoints[0]

    def health(self) -> dict:
        return {url:dict(row) for url,row in self._health.items()}

    def _invoke(self, method: str, *args, **kwargs):
        now=float(self._clock())
        attempted=0
        last_error=None
        for url in self.endpoints:
            row=self._health[url]
            if now < float(row['cooldown_until']):
                continue
            attempted += 1
            try:
                result=getattr(self.clients[url],method)(*args,**kwargs)
            except RpcError as exc:
                if not _failover_eligible(exc):
                    raise
                row['failures']=int(row['failures'])+1
                row['last_error']=str(exc)
                row['cooldown_until']=now+self.cooldown_s
                last_error=exc
                continue
            row['successes']=int(row['successes'])+1
            row['failures']=0
            row['cooldown_until']=0.0
            row['last_error']=None
            self.current_endpoint=url
            return result
        if attempted == 0:
            raise RpcError('all RPC endpoints are cooling down')
        raise RpcError(f'all RPC endpoints failed: {last_error}')

    def call(self, method, params):
        return self._invoke('call',method,params)

    def batch(self, calls):
        return self._invoke('batch',calls)

    def batch_eth_call_results(self, calls, *, block='latest', max_batch=100):
        return self._invoke('batch_eth_call_results',calls,block=block,max_batch=max_batch)

    def batch_eth_call(self, calls, *, block='latest', max_batch=100):
        return self._invoke('batch_eth_call',calls,block=block,max_batch=max_batch)

    def chain_id(self):
        return self._invoke('chain_id')

    def block_number(self):
        return self._invoke('block_number')

    def get_code(self, address, block='latest'):
        return self._invoke('get_code',address,block=block)

    def eth_call(self, to, data, block='latest'):
        return self._invoke('eth_call',to,data,block=block)

    def gas_price(self):
        return self._invoke('gas_price')
