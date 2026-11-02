// 程序化背景交互系统
(function() {
  'use strict';

  let mouseX = 0;
  let mouseY = 0;
  let cursorGlow = null;
  let coordinateSystem = null;
  let gridHighlight = null;
  const GRID_SIZE = 80; // 网格单元大小
  
  // 初始化
  function init() {
    createElements();
    attachEventListeners();
    console.log('✨ 程序化交互系统已激活');
  }
  
  // 创建交互元素
  function createElements() {
    // 光标跟随光晕
    if (!document.querySelector('#cursor-glow')) {
      cursorGlow = document.createElement('div');
      cursorGlow.id = 'cursor-glow';
      cursorGlow.style.cssText = `
        position: fixed;
        width: 400px;
        height: 400px;
        background: radial-gradient(circle, rgba(232, 93, 63, 0.12) 0%, rgba(74, 147, 199, 0.08) 40%, transparent 70%);
        border-radius: 50%;
        pointer-events: none;
        opacity: 0;
        transition: opacity 0.3s ease;
        z-index: 1;
        mix-blend-mode: multiply;
        filter: blur(40px);
        transform: translate(-50%, -50%);
      `;
      document.body.appendChild(cursorGlow);
    }
    
    // 坐标系
    if (!document.querySelector('#coordinate-system')) {
      coordinateSystem = document.createElement('div');
      coordinateSystem.id = 'coordinate-system';
      coordinateSystem.className = 'coordinate-system';
      coordinateSystem.innerHTML = '<div class="h-line"></div><div class="v-line"></div>';
      coordinateSystem.style.cssText = `
        position: fixed;
        inset: 0;
        pointer-events: none;
        z-index: 2;
        opacity: 0;
        transition: opacity 0.3s ease;
      `;
      document.body.appendChild(coordinateSystem);
      
      const hLine = coordinateSystem.querySelector('.h-line');
      const vLine = coordinateSystem.querySelector('.v-line');
      
      hLine.style.cssText = `
        position: absolute;
        left: 0;
        right: 0;
        height: 1px;
        background: linear-gradient(90deg, transparent 0%, rgba(232, 93, 63, 0.3) 50%, transparent 100%);
        transition: all 0.1s ease-out;
      `;
      
      vLine.style.cssText = `
        position: absolute;
        top: 0;
        bottom: 0;
        width: 1px;
        background: linear-gradient(180deg, transparent 0%, rgba(74, 147, 199, 0.3) 50%, transparent 100%);
        transition: all 0.1s ease-out;
      `;
    }
    
    // 网格高亮
    if (!document.querySelector('#grid-highlight')) {
      gridHighlight = document.createElement('div');
      gridHighlight.id = 'grid-highlight';
      gridHighlight.className = 'grid-highlight';
      gridHighlight.style.cssText = `
        position: fixed;
        width: ${GRID_SIZE}px;
        height: ${GRID_SIZE}px;
        border: 2px solid rgba(232, 93, 63, 0.4);
        background: rgba(232, 93, 63, 0.05);
        pointer-events: none;
        opacity: 0;
        transition: opacity 0.2s ease;
        z-index: 1;
        box-shadow: 0 0 20px rgba(232, 93, 63, 0.2), inset 0 0 20px rgba(232, 93, 63, 0.1);
      `;
      document.body.appendChild(gridHighlight);
    }
  }
  
  // 绑定事件监听
  function attachEventListeners() {
    // 鼠标移动
    document.addEventListener('mousemove', handleMouseMove);
    
    // 鼠标进入/离开
    document.addEventListener('mouseenter', () => {
      if (cursorGlow) cursorGlow.style.opacity = '1';
      if (coordinateSystem) coordinateSystem.style.opacity = '1';
      if (gridHighlight) gridHighlight.style.opacity = '1';
    });
    
    document.addEventListener('mouseleave', () => {
      if (cursorGlow) cursorGlow.style.opacity = '0';
      if (coordinateSystem) coordinateSystem.style.opacity = '0';
      if (gridHighlight) gridHighlight.style.opacity = '0';
    });
    
    // 点击波纹
    document.addEventListener('click', handleClick);
    
    // 滚动时创建数据流
    let scrollTimeout;
    document.addEventListener('scroll', () => {
      clearTimeout(scrollTimeout);
      scrollTimeout = setTimeout(() => {
        createDataStream(window.innerWidth / 2, window.scrollY + window.innerHeight / 2);
      }, 100);
    }, { passive: true });
    
    // 每5秒随机扫描线
    setInterval(() => {
      if (Math.random() > 0.5) {
        createScanLine();
      }
    }, 5000);
  }
  
  // 鼠标移动处理
  function handleMouseMove(e) {
    mouseX = e.clientX;
    mouseY = e.clientY;
    
    // 更新光标光晕
    if (cursorGlow) {
      cursorGlow.style.left = mouseX + 'px';
      cursorGlow.style.top = mouseY + 'px';
    }
    
    // 更新坐标线
    if (coordinateSystem) {
      const hLine = coordinateSystem.querySelector('.h-line');
      const vLine = coordinateSystem.querySelector('.v-line');
      if (hLine) hLine.style.top = mouseY + 'px';
      if (vLine) vLine.style.left = mouseX + 'px';
    }
    
    // 更新网格高亮
    if (gridHighlight) {
      const gridX = Math.floor(mouseX / GRID_SIZE) * GRID_SIZE;
      const gridY = Math.floor(mouseY / GRID_SIZE) * GRID_SIZE;
      gridHighlight.style.left = gridX + 'px';
      gridHighlight.style.top = gridY + 'px';
    }
  }
  
  // 点击波纹
  function handleClick(e) {
    const ripple = document.createElement('div');
    ripple.className = 'click-ripple';
    ripple.style.cssText = `
      position: fixed;
      left: ${e.clientX}px;
      top: ${e.clientY}px;
      border: 2px solid rgba(232, 93, 63, 0.6);
      border-radius: 50%;
      pointer-events: none;
      z-index: 3;
      animation: ripple-expand 0.8s ease-out;
    `;
    
    document.body.appendChild(ripple);
    
    setTimeout(() => {
      ripple.remove();
    }, 800);
    
    // 随机创建数据流
    if (Math.random() > 0.6) {
      createDataStream(e.clientX, e.clientY);
    }
  }
  
  // 创建数据流
  function createDataStream(x, y) {
    const stream = document.createElement('div');
    stream.className = 'data-stream';
    stream.style.cssText = `
      position: fixed;
      left: ${x}px;
      top: ${y - 40}px;
      width: 2px;
      height: 80px;
      background: linear-gradient(180deg, transparent, rgba(74, 147, 199, 0.6), transparent);
      pointer-events: none;
      opacity: 0;
      z-index: 1;
      animation: stream-flow 2s ease-in-out;
    `;
    
    document.body.appendChild(stream);
    
    setTimeout(() => {
      stream.remove();
    }, 2000);
  }
  
  // 创建扫描线
  function createScanLine() {
    const scanLine = document.createElement('div');
    scanLine.className = 'scan-line';
    scanLine.style.cssText = `
      position: fixed;
      left: 0;
      right: 0;
      top: 0;
      height: 2px;
      background: linear-gradient(90deg, transparent, rgba(232, 93, 63, 0.6), transparent);
      pointer-events: none;
      opacity: 0;
      z-index: 1;
      box-shadow: 0 0 20px rgba(232, 93, 63, 0.4);
      animation: scan-move 3s ease-in-out;
    `;
    
    document.body.appendChild(scanLine);
    
    setTimeout(() => {
      scanLine.remove();
    }, 3000);
  }
  
  // 页面加载完成后初始化
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
  
  // 导出控制台命令
  window.FishBotInteractive = {
    createScanLine: createScanLine,
    createDataStream: (x, y) => createDataStream(x || window.innerWidth / 2, y || window.innerHeight / 2),
    info: () => {
      console.log('%c🎨 FishBot 程序化交互系统', 'color: #e85d3f; font-size: 16px; font-weight: bold;');
      console.log('• 光标跟随光晕 ✓');
      console.log('• 坐标系指示器 ✓');
      console.log('• 网格单元高亮 ✓');
      console.log('• 点击波纹效果 ✓');
      console.log('• 数据流动效果 ✓');
      console.log('• 随机扫描线 ✓');
      console.log('%c移动鼠标体验程序化交互！', 'color: #4a93c7; font-style: italic;');
    }
  };
  
  // 自动显示信息
  setTimeout(() => {
    window.FishBotInteractive.info();
  }, 1000);
  
})();
