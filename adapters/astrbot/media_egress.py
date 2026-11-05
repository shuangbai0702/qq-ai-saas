"""Public media downloads using the existing pinned DNS/socket policy."""
import asyncio,ipaddress,os,tempfile
from pathlib import Path
from urllib.parse import urlsplit,urljoin
import httpx
from model_egress import GuardedBackend,public_ip

MEDIA_BYTES=32*1024*1024
MEDIA_SECONDS=45
MAX_REDIRECTS=3

def valid_url(value):
    if not isinstance(value,str) or value!=value.strip() or '\\' in value or any(ord(c)<=32 or ord(c)==127 for c in value):return False
    try:
        u=urlsplit(value)
        if u.scheme not in ('https','http') or not u.hostname or u.username is not None or u.password is not None or u.fragment:return False
        if u.port is not None and not 1<=u.port<=65535:return False
        if u.hostname.lower().rstrip('.') in ('localhost','localhost.localdomain'):return False
        try:return public_ip(str(ipaddress.ip_address(u.hostname)))
        except ValueError:return True # DNS is checked and pinned at the actual socket.
    except ValueError:return False

async def download_file(url,path,show_progress=False,progress_callback=None,allow_insecure_ssl_fallback=False,*,max_bytes=None):
    if not valid_url(url):raise ValueError('media_url_denied')
    target=Path(path);temporary=None
    byte_limit=min(MEDIA_BYTES,max_bytes if max_bytes is not None else MEDIA_BYTES)
    if byte_limit<=0:raise ValueError('media_size_limit')
    from astrbot.core.utils.network_utils import _SYSTEM_SSL_CTX
    transport=httpx.AsyncHTTPTransport(verify=_SYSTEM_SSL_CTX,trust_env=False,retries=0)
    transport._pool._network_backend=GuardedBackend(transport._pool._network_backend)
    try:
        async with asyncio.timeout(MEDIA_SECONDS):
            async with httpx.AsyncClient(transport=transport,trust_env=False,follow_redirects=False,timeout=httpx.Timeout(15,connect=10)) as client:
                current=url
                for redirect in range(MAX_REDIRECTS+1):
                    if not valid_url(current):raise ValueError('media_redirect_denied')
                    async with client.stream('GET',current,headers={'Accept-Encoding':'identity'}) as response:
                        if response.status_code in (301,302,303,307,308):
                            if redirect==MAX_REDIRECTS or not response.headers.get('location'):raise ValueError('media_redirect_limit')
                            current=urljoin(current,response.headers['location']);continue
                        response.raise_for_status()
                        if response.headers.get('content-encoding','identity').lower()!='identity':raise ValueError('media_encoding_denied')
                        length=response.headers.get('content-length','')
                        if length and (not length.isdecimal() or len(length)>12 or int(length)>byte_limit):raise ValueError('media_size_limit')
                        fd,temporary=tempfile.mkstemp(prefix='.media-',dir=target.parent)
                        with os.fdopen(fd,'wb') as file:
                            size=0
                            async for chunk in response.aiter_bytes(65536):
                                size+=len(chunk)
                                if size>byte_limit:raise ValueError('media_size_limit')
                                file.write(chunk)
                        os.replace(temporary,target);temporary=None
                        return
    finally:
        if temporary:
            try:os.unlink(temporary)
            except FileNotFoundError:pass

def install_downloads():
    from astrbot.core.utils import io
    from astrbot.core.message import components
    io.download_file=download_file
    components.download_file=download_file
