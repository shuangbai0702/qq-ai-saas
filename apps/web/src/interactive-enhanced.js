// 增强的背景程序化交互系统
(function() {
  'use strict';

  let mouseX = 0;
  let mouseY = 0;
  
  // 初始化
  function init() {
    createInteractiveElements();
    attachEventListeners();
    startAmbientEffects();
    console.log('%c✨ 增强交互系统已激活', 'color: #e85d3f; font-size: 14px; font-weight: bold;');
  }
  
  // 创建交互元素
  function createInteractiveElements() {
    // 光标跟随光晕
    const cursorGlow = document.createElement('div');
    cursorGlow.id = 'cursor-glow';
    cursorGlow.style.cssText = `
      position: fixed;
      width: 300px;
      height: 300px;
      background: radial-gradient(circle, rgba(232, 93, 63, 0.08) 0%, transparent 70%);
      border-radius: 50%;
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.5s ease;
      z-index: 9998;
      mix-blend-mode: multiply;
      filter: blur(40px);
      transform: translate(-50%, -50%);
    `;
    document.body.appendChild(cursorGlow);
    
    // 网格高亮
    const gridHighlight = document.createElement('div');
    gridHighlight.id = 'grid-highlight';
    gridHighlight.style.cssText = `
      position: fixed;
      width: 80px;
      height: 80px;
      border: 2px solid rgba(232, 93, 63, 0.3);
      pointer-events: none;
      opacity: 0;
      transition: opacity 0.3s ease, left 0.1s ease, top 0.1s ease;
      z-index: 1;
      box-shadow: 
        0 0 20px rgba(232, 93, 63, 0.15),
        inset 0 0 20px rgba(232, 93, 63, 0.08);
    `;
    document.body.appendChild(gridHighlight);
    
    // 添加动画样式
    if (!document.querySelector('#interactive-styles')) {
      const style = document.createElement('style');
      style.id = 'interactive-styles';
      style.textContent = `
        @keyframes ripple-expand {
          0% {
            width: 0;
            height: 0;
            margin: 0;
            opacity: 1;
          }
          100% {
            width: 200px;
            height: 200px;
            margin: -100px;
            opacity: 0;
          }
        }
        
        @keyframes particle-rise {
          0% {
            opacity: 0;
            transform: translateY(0) scale(0);
          }
          50% {
            opacity: 1;
          }
          100% {
            opacity: 0;
            transform: translateY(-100px) scale(1);
          }
        }
        
        @keyframes data-stream {
          0% {
            opacity: 0;
            transform: translateY(-50px);
          }
          20% {
            opacity: 1;
          }
          80% {
            opacity: 1;
          }
          100% {
            opacity: 0;
            transform: translateY(100px);
          }
        }
        
        @keyframes scan-horizontal {
          0% {
            opacity: 0;
            left: -100%;
          }
          10% {
            opacity: 0.6;
          }
          90% {
            opacity: 0.6;
          }
          100% {
            opacity: 0;
            left: 100%;
          }
        }
      `;
      document.head.appendChild(style);
    }
  }
  
  // 绑定事件监听
  function attachEventListeners() {
    const cursorGlow = document.getElementById('cursor-glow');
    const gridHighlight = document.getElementById('grid-highlight');
    
    // 鼠标移动
    document.addEventListener('mousemove', (e) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
      
      // 更新光标光晕
      if (cursorGlow) {
        cursorGlow.style.left = mouseX + 'px';
        cursorGlow.style.top = mouseY + 'px';
      }
      
      // 更新网格高亮
      if (gridHighlight) {
        const gridX = Math.floor(mouseX / 80) * 80;
        const gridY = Math.floor((mouseY + window.scrollY) / 80) * 80 - window.scrollY;
        gridHighlight.style.left = gridX + 'px';
        gridHighlight.style.top = gridY + 'px';
      }
      
      // 随机生成粒子
      if (Math.random() > 0.98) {
        createParticle(mouseX, mouseY);
      }
    });
    
    // 鼠标进入
    document.addEventListener('mouseenter', () => {
      if (cursorGlow) cursorGlow.style.opacity = '1';
      if (gridHighlight) gridHighlight.style.opacity = '1';
    });
    
    // 鼠标离开
    document.addEventListener('mouseleave', () => {
      if (cursorGlow) cursorGlow.style.opacity = '0';
      if (gridHighlight) gridHighlight.style.opacity = '0';
    });
    
    // 点击波纹
    document.addEventListener('click', (e) => {
      if (e.target.closest('button') || e.target.closest('a')) {
        return;
      }
      
      createRipple(e.clientX, e.clientY);
      
      // 随机创建数据流
      if (Math.random() > 0.5) {
        createDataStream(e.clientX, e.clientY);
      }
    });
    
    // 滚动时创建效果
    let scrollTimeout;
    document.addEventListener('scroll', () => {
      clearTimeout(scrollTimeout);
      scrollTimeout = setTimeout(() => {
        if (Math.random() > 0.6) {
          const x = Math.random() * window.innerWidth;
          const y = window.scrollY + Math.random() * window.innerHeight;
          createDataStream(x, y);
        }
      }, 100);
    }, { passive: true });
  }
  
  // 创建点击波纹
  function createRipple(x, y) {
    const ripple = document.createElement('div');
    ripple.style.cssText = `
      position: fixed;
      left: ${x}px;
      top: ${y}px;
      border: 2px solid rgba(232, 93, 63, 0.5);
      border-radius: 50%;
      pointer-events: none;
      z-index: 9999;
      animation: ripple-expand 0.8s ease-out;
    `;
    
    document.body.appendChild(ripple);
    setTimeout(() => ripple.remove(), 800);
  }
  
  // 创建粒子
  function createParticle(x, y) {
    const particle = document.createElement('div');
    const size = Math.random() * 4 + 2;
    const color = Math.random() > 0.5 ? 'rgba(232, 93, 63, 0.6)' : 'rgba(74, 147, 199, 0.6)';
    
    particle.style.cssText = `
      position: fixed;
      left: ${x}px;
      top: ${y}px;
      width: ${size}px;
      height: ${size}px;
      background: ${color};
      border-radius: 50%;
      pointer-events: none;
      z-index: 9997;
      animation: particle-rise ${1 + Math.random()}s ease-out;
    `;
    
    document.body.appendChild(particle);
    setTimeout(() => particle.remove(), 2000);
  }
  
  // 创建数据流
  function createDataStream(x, y) {
    const stream = document.createElement('div');
    stream.style.cssText = `
      position: fixed;
      left: ${x}px;
      top: ${y}px;
      width: 2px;
      height: 60px;
      background: linear-gradient(180deg, transparent, rgba(74, 147, 199, 0.6), transparent);
      pointer-events: none;
      z-index: 9997;
      animation: data-stream 2s ease-in-out;
    `;
    
    document.body.appendChild(stream);
    setTimeout(() => stream.remove(), 2000);
  }
  
  // 创建扫描线
  function createScanLine() {
    const scan = document.createElement('div');
    scan.style.cssText = `
      position: fixed;
      top: ${Math.random() * window.innerHeight}px;
      width: 100%;
      height: 2px;
      background: linear-gradient(90deg, transparent, rgba(232, 93, 63, 0.4), transparent);
      pointer-events: none;
      z-index: 9997;
      box-shadow: 0 0 20px rgba(232, 93, 63, 0.3);
      animation: scan-horizontal 3s ease-in-out;
    `;
    
    document.body.appendChild(scan);
    setTimeout(() => scan.remove(), 3000);
  }
  
  // 启动环境效果
  function startAmbientEffects() {
    // 随机扫描线
    setInterval(() => {
      if (Math.random() > 0.6) {
        createScanLine();
      }
    }, 5000);
    
    // 随机粒子
    setInterval(() => {
      if (document.hidden) return;
      
      const x = Math.random() * window.innerWidth;
      const y = Math.random() * window.innerHeight + window.scrollY;
      
      if (Math.random() > 0.7) {
        createParticle(x, y);
      }
    }, 3000);
  }
  
  // 初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  
})();
