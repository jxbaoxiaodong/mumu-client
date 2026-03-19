/*
  成长记录 - 主 JavaScript 文件
  处理页面交互和功能
*/

// 全局变量
let currentDate = new Date().toISOString().split('T')[0];

// 页面加载完成
document.addEventListener('DOMContentLoaded', function() {
    console.log('成长记录系统已加载');
    
    // 初始化工具提示
    initTooltips();
    
    // 初始化瀑布流
    initMasonry();
    
    // 初始化日历（如果函数存在）
    if (typeof initCalendar === 'function') {
        initCalendar();
    }
    
    // 检查配额状态
    checkQuotaStatus();
    
    // 显示欢迎消息
    showWelcomeMessage();
});

// 初始化工具提示
function initTooltips() {
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
}

// 初始化瀑布流布局
function initMasonry() {
    const grid = document.querySelector('#masonry-grid');
    if (grid) {
        // 等待图片加载完成
        const images = grid.querySelectorAll('img');
        let loadedCount = 0;
        
        images.forEach(img => {
            if (img.complete) {
                loadedCount++;
            } else {
                img.addEventListener('load', () => {
                    loadedCount++;
                    if (loadedCount === images.length) {
                        createMasonry();
                    }
                });
            }
        });
        
        if (loadedCount === images.length) {
            createMasonry();
        }
    }
}

function createMasonry() {
    const grid = document.querySelector('#masonry-grid');
    if (!grid) return;
    
    // 使用 CSS Grid 替代 Masonry.js
    grid.style.display = 'grid';
    grid.style.gridTemplateColumns = 'repeat(auto-fill, minmax(250px, 1fr))';
    grid.style.gap = '20px';
}

// 初始化日历占位（实际实现在 calendar.html 中）
function initCalendarPlaceholder() {
    console.log('日历功能已初始化');
}

// 检查配额状态
function checkQuotaStatus() {
    fetch('/api/quota/status')
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                updateQuotaDisplay(data);
            }
        })
        .catch(error => {
            console.error('获取配额状态失败:', error);
        });
}

// 更新配额显示
function updateQuotaDisplay(data) {
    const quotaDetails = data.quota_details;
    if (!quotaDetails) return;
    
    // 更新页面上的配额显示
    const quotaElements = document.querySelectorAll('.quota-display');
    quotaElements.forEach(element => {
        element.textContent = `${quotaDetails.used_today}/${quotaDetails.daily_limit}`;
        
        // 根据使用率添加样式
        const usageRate = quotaDetails.used_today / quotaDetails.daily_limit;
        if (usageRate > 0.8) {
            element.classList.add('text-warning');
        }
        if (usageRate > 0.95) {
            element.classList.add('text-danger');
        }
    });
}

// 显示欢迎消息
function showWelcomeMessage() {
    const hour = new Date().getHours();
    let greeting = '';
    
    if (hour < 6) {
        greeting = '夜深了，还在记录宝宝的成长吗？';
    } else if (hour < 12) {
        greeting = '早上好！新的一天开始了！';
    } else if (hour < 18) {
        greeting = '下午好！今天有什么新发现吗？';
    } else {
        greeting = '晚上好！今天过得怎么样？';
    }
    
    // 可以在这里显示通知
    console.log(greeting);
}

// 显示通知
function showNotification(message, type = 'info') {
    // 检查是否已有通知容器
    let notificationContainer = document.getElementById('notification-container');
    if (!notificationContainer) {
        notificationContainer = document.createElement('div');
        notificationContainer.id = 'notification-container';
        notificationContainer.style.cssText = `
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 9999;
            max-width: 350px;
        `;
        document.body.appendChild(notificationContainer);
    }
    
    // 创建通知
    const notification = document.createElement('div');
    notification.className = `alert alert-${type} alert-dismissible fade show`;
    notification.style.cssText = `
        margin-bottom: 10px;
        box-shadow: 0 4px 12px rgba(0,0,0,0.15);
        animation: slideIn 0.3s ease;
    `;
    
    // 设置图标
    let icon = 'info-circle';
    if (type === 'success') icon = 'check-circle';
    if (type === 'error') icon = 'exclamation-triangle';
    if (type === 'warning') icon = 'exclamation-circle';
    
    notification.innerHTML = `
        <i class="fas fa-${icon} me-2"></i>
        ${message}
        <button type="button" class="btn-close" data-bs-dismiss="alert"></button>
    `;
    
    // 添加到容器
    notificationContainer.appendChild(notification);
    
    // 5秒后自动移除
    setTimeout(() => {
        if (notification.parentNode) {
            notification.remove();
        }
    }, 5000);
}

// 复制到剪贴板
function copyToClipboard(text) {
    navigator.clipboard.writeText(text)
        .then(() => {
            showNotification('已复制到剪贴板', 'success');
        })
        .catch(err => {
            showNotification('复制失败: ' + err, 'error');
        });
}

// 分享功能
function shareContent(title, text, url) {
    if (navigator.share) {
        navigator.share({
            title: title,
            text: text,
            url: url
        })
        .then(() => console.log('分享成功'))
        .catch(error => console.log('分享失败:', error));
    } else {
        // 降级方案：复制链接
        copyToClipboard(url);
    }
}

// 照片操作
function editPhotoCaption(photoId) {
    const newCaption = prompt('请输入新的照片标注：');
    if (newCaption !== null) {
        fetch(`/api/photo/${photoId}/caption`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({caption: newCaption})
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showNotification('标注已更新', 'success');
                setTimeout(() => location.reload(), 1000);
            } else {
                showNotification('更新失败: ' + data.message, 'error');
            }
        });
    }
}

function deletePhoto(photoId) {
    if (confirm('确定要删除这张照片吗？此操作不可撤销。')) {
        fetch(`/api/photo/${photoId}`, {
            method: 'DELETE'
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                showNotification('照片已删除', 'success');
                setTimeout(() => location.reload(), 1000);
            } else {
                showNotification('删除失败: ' + data.message, 'error');
            }
        });
    }
}

// 日期导航
function navigateToDate(dateStr) {
    window.location.href = `/date/${dateStr}`;
}

// 生成今日日志
function generateTodayLog() {
    const modal = new bootstrap.Modal(document.getElementById('aiModal'));
    modal.show();
}

// 上传照片
function uploadPhotos() {
    const modal = new bootstrap.Modal(document.getElementById('uploadModal'));
    modal.show();
}

// 添加留言
function addMessage() {
    const modal = new bootstrap.Modal(document.getElementById('messageModal'));
    modal.show();
}

// 查看日历
function viewCalendar() {
    const modal = new bootstrap.Modal(document.getElementById('calendarModal'));
    modal.show();
}

// 格式化日期
function formatDate(dateStr) {
    const date = new Date(dateStr);
    const options = { 
        year: 'numeric', 
        month: 'long', 
        day: 'numeric',
        weekday: 'long'
    };
    return date.toLocaleDateString('zh-CN', options);
}

// 格式化时间
function formatTime(dateStr) {
    const date = new Date(dateStr);
    return date.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit'
    });
}

// 加载更多内容
function loadMoreContent(type) {
    const loader = document.getElementById(`${type}-loader`);
    if (loader) {
        loader.style.display = 'block';
        
        // 模拟加载延迟
        setTimeout(() => {
            // 这里应该调用API加载更多数据
            loader.style.display = 'none';
            showNotification('已加载更多内容', 'success');
        }, 1000);
    }
}

// 搜索功能
function searchContent(query) {
    if (!query.trim()) return;
    
    showNotification(`正在搜索: ${query}`, 'info');
    
    // 这里应该实现搜索功能
    // fetch(`/api/search?q=${encodeURIComponent(query)}`)
    //   .then(response => response.json())
    //   .then(data => {
    //       // 处理搜索结果
    //   });
}

// 键盘快捷键
document.addEventListener('keydown', function(event) {
    // Ctrl/Cmd + N: 新建日志
    if ((event.ctrlKey || event.metaKey) && event.key === 'n') {
        event.preventDefault();
        generateTodayLog();
    }
    
    // Ctrl/Cmd + U: 上传照片
    if ((event.ctrlKey || event.metaKey) && event.key === 'u') {
        event.preventDefault();
        uploadPhotos();
    }
    
    // Ctrl/Cmd + M: 添加留言
    if ((event.ctrlKey || event.metaKey) && event.key === 'm') {
        event.preventDefault();
        addMessage();
    }
    
});

// 添加 CSS 动画
const style = document.createElement('style');
style.textContent = `
    @keyframes slideIn {
        from {
            transform: translateX(100%);
            opacity: 0;
        }
        to {
            transform: translateX(0);
            opacity: 1;
        }
    }
    
    @keyframes fadeIn {
        from { opacity: 0; }
        to { opacity: 1; }
    }
    
    .animate-fade-in {
        animation: fadeIn 0.5s ease;
    }
    
    .animate-slide-in {
        animation: slideIn 0.3s ease;
    }
`;
document.head.appendChild(style);

// 导出函数供其他脚本使用
window.CZRZ = {
    showNotification,
    copyToClipboard,
    shareContent,
    formatDate,
    formatTime,
    navigateToDate,
    generateTodayLog,
    uploadPhotos,
    addMessage,
    viewCalendar
};