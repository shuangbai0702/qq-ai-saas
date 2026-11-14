// This small classic script also works when the application module cannot be downloaded or parsed.
(function(){setTimeout(function(){var root=document.getElementById('root'),status=document.getElementById('boot-status');if(root&&!root.dataset.mounted&&status){status.textContent='页面资源下载较慢或加载失败，请检查网络后点击重新加载。';status.setAttribute('role','alert')}},15000)})();
