/*
  成长记录 - 主 JavaScript 文件
  处理页面交互和功能
*/

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
