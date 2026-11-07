"""Guard the actual SDK socket, not just the saved Base URL. Trusted plugins only."""
import asyncio,ipaddress,os,socket,time
from urllib.parse import urlsplit
import httpx,httpcore

MAX_RESPONSE_BYTES=1024*1024

class LimitedStream(httpx.AsyncByteStream):
    def __init__(self,stream,wire=None,status=None):self.stream=stream;self.closed=False;self.wire=wire;self.status=status
    async def __aiter__(self):
        from model_cache import transport_result
        size=0
        try:
            async for chunk in self.stream:
                size+=len(chunk)
                if size>MAX_RESPONSE_BYTES:
                    raise httpx.ReadError('Model response exceeded the hosted byte limit')
                if self.wire:
                    value=self.wire['attempt']
                    value.setdefault('firstResponseMs',round((time.monotonic()-value['started'])*1000))
                yield chunk
            transport_result(self.wire,'completed' if self.status and self.status<400 else 'failed',http_status=self.status)
        except BaseException as error:
            transport_result(self.wire,'canceled' if isinstance(error,asyncio.CancelledError) else 'failed',type(error).__name__,self.status)
            await self.aclose()
            raise
    async def aclose(self):
        if not self.closed:
            self.closed=True
            from model_cache import transport_result
            try:await self.stream.aclose()
            finally:transport_result(self.wire,'unknown','response_not_exhausted',self.status)

class GuardedTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self,request):
        from model_cache import observe_wire_request,transport_started,transport_result
        observe_wire_request(request)
        from model_request_capture import begin,CaptureStream
        capture=begin(request)
        # Bound decoded memory as well as wire bytes. A malicious provider must
        # not turn a tiny compressed reply into a shared-process memory bomb.
        request.headers['Accept-Encoding']='identity'
        wire=transport_started()
        try:response=await super().handle_async_request(request)
        except BaseException as error:
            transport_result(wire,'canceled' if isinstance(error,asyncio.CancelledError) else 'unknown',type(error).__name__)
            raise
        if wire:wire['attempt']['headersReceived']=True
        length=response.headers.get('content-length','')
        if response.headers.get('content-encoding','identity').lower()!='identity' or (length.isdecimal() and (len(length)>12 or int(length)>MAX_RESPONSE_BYTES)):
            await response.aclose()
            transport_result(wire,'failed','response_size_or_encoding',response.status_code)
            raise httpx.ReadError('Model response encoding or size is not supported')
        response.stream=LimitedStream(response.stream,wire,response.status_code)
        if capture:
            capture.status=response.status_code
            response.stream=CaptureStream(response.stream,capture)
        return response

def public_ip(value):
    address=ipaddress.ip_address(value)
    if not address.is_global or address.is_multicast:return False
    if isinstance(address,ipaddress.IPv6Address):
        return address.ipv4_mapped is None and address in ipaddress.ip_network('2000::/3') and address not in ipaddress.ip_network('2001::/23') and address not in ipaddress.ip_network('2001:db8::/32') and address not in ipaddress.ip_network('2002::/16')
    return address not in ipaddress.ip_network('192.0.0.0/24') and address not in ipaddress.ip_network('192.88.99.0/24')

class GuardedBackend:
    def __init__(self,backend,trusted=False):self.backend,self.trusted=backend,trusted
    async def connect_tcp(self,host,port,timeout=None,local_address=None,socket_options=None):
        host=host.decode() if isinstance(host,bytes) else host
        answers=await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM),timeout=min(timeout or 10,10))
        addresses=list(dict.fromkeys(a[4][0] for a in answers))
        if not addresses or (self.trusted!=(host,port) and any(not public_ip(a) for a in addresses)):
            raise httpx.ConnectError('Model endpoint cannot access local, private or reserved addresses')
        # HTTPcore performs TLS with the request's original host after receiving
        # this stream. Connecting to a checked numeric address prevents rebinding.
        error=None
        for address in addresses[:4]:
            try:return await self.backend.connect_tcp(address,port,timeout=min(timeout or 10,10),local_address=local_address,socket_options=socket_options)
            except (OSError,httpcore.ConnectError,httpcore.ConnectTimeout) as caught:error=caught
        raise httpx.ConnectError('Model connection failed') from error
    async def connect_unix_socket(self,*args,**kwargs):raise httpx.ConnectError('Unix model sockets are disabled')
    async def sleep(self,seconds):await self.backend.sleep(seconds)

def client(config):
    url=urlsplit(config.get('api_base',''))
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Invalid model Base URL')
    port=url.port or (443 if url.scheme=='https' else 80)
    authority='['+url.hostname+']' if ':' in url.hostname else url.hostname
    origin=url.scheme+'://'+authority+((':'+str(port)) if port!=(443 if url.scheme=='https' else 80) else '')
    trusted=origin in [x.strip() for x in os.getenv('FISHBOT_MODEL_ALLOWED_PRIVATE_ORIGINS','').split(',')]
    from astrbot.core.utils.network_utils import _SYSTEM_SSL_CTX
    transport=GuardedTransport(verify=_SYSTEM_SSL_CTX,trust_env=False,retries=0,limits=httpx.Limits(max_connections=8,max_keepalive_connections=2))
    pool=transport._pool
    if not hasattr(pool,'_network_backend'):raise RuntimeError('Pinned HTTPcore network backend changed; refusing unguarded model traffic')
    pool._network_backend=GuardedBackend(pool._network_backend,(url.hostname,port) if trusted else None)
    return httpx.AsyncClient(transport=transport,trust_env=False,follow_redirects=False)

def install():
    from astrbot.core.provider.sources.openai_source import ProviderOpenAIOfficial
    ProviderOpenAIOfficial._create_http_client=lambda self,config:client(config)
