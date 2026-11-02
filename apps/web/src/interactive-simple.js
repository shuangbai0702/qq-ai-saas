// 轻量级程序化交互系统 - 不影响滚动
(function() {
  'use strict';

  let mouseX = 0;
  let mouseY = 0;
  
  // 初始化
  function init() {
    attachEventListeners();
    console.log('%c✨ 程序化交互系统已激活', 'color: #e85d3f; font-size: 14px; font-weight: bold;');
  }
  
  // 绑定事件监听
  function attachEventListeners() {
    // 点击波纹
    document.addEventListener('click', handleClick);
  }
  
  // 点击波纹
  function handleClick(e) {
    // 跳过按钮和链接点击
    if (e.target.closest('button') || e.target.closest('a')) {
      return;
    }
    
    const ripple = document.createElement('div');
    ripple.style.cssText = `
      position: fixed;
      left: ${e.clientX}px;
      top: ${e.clientY}px;
      width: 0;
      height: 0;
      border: 2px solid rgba(232, 93, 63, 0.5);
      border-radius: 50%;
      pointer-events: none;
      z-index: 9999;
      animation: ripple-expand 0.6s ease-out;
    `;
    
    const style = document.createElement('style');
    style.textContent = `
      @keyframes ripple-expand {
        0% {
          width: 0;
          height: 0;
          margin: 0;
          opacity: 1;
        }
        100% {
          width: 150px;
          height: 150px;
          margin: -75px;
          opacity: 0;
        }
      }
    `;
    
    if (!document.querySelector('#ripple-style')) {
      style.id = 'ripple-style';
      document.head.appendChild(style);
    }
    
    document.body.appendChild(ripple);
    
    setTimeout(() => {
      ripple.remove();
    }, 600);
  }
  
  // 页面加载完成后初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  
})();
