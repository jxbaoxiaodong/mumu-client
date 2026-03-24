#!/usr/bin/env python3
"""
数据清理脚本：删除重复的照片描述和语音记录
"""

import sqlite3
from pathlib import Path
import sys

def cleanup_user_db(db_path: Path):
    """清理单个用户数据库中的重复数据"""
    if not db_path.exists():
        print(f"数据库不存在: {db_path}")
        return
    
    print(f"\n清理数据库: {db_path}")
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # 1. 清理重复的语音记录（保留最新的一条）
    try:
        # 查找重复的语音记录
        cursor.execute("""
            SELECT date, video_path, COUNT(*) as cnt, MAX(id) as max_id
            FROM speech_records
            GROUP BY date, video_path
            HAVING cnt > 1
        """)
        duplicates = cursor.fetchall()
        
        if duplicates:
            print(f"  发现 {len(duplicates)} 组重复语音记录")
            for date, video_path, count, max_id in duplicates:
                # 删除该组中除了最新一条之外的所有记录
                cursor.execute("""
                    DELETE FROM speech_records
                    WHERE date = ? AND video_path = ? AND id != ?
                """, (date, video_path, max_id))
                print(f"    清理 {date} 的视频: {Path(video_path).name} (保留 id={max_id}, 删除 {count-1} 条)")
            conn.commit()
            print(f"  ✓ 语音记录去重完成")
        else:
            print("  ✓ 没有重复的语音记录")
    except Exception as e:
        print(f"  ✗ 清理语音记录失败: {e}")
    
    # 2. 清理重复的照片描述（保留最新的一条）
    try:
        # 查找重复的照片描述
        cursor.execute("""
            SELECT date, file_path, COUNT(*) as cnt, MAX(id) as max_id
            FROM photo_descriptions
            GROUP BY date, file_path
            HAVING cnt > 1
        """)
        duplicates = cursor.fetchall()
        
        if duplicates:
            print(f"  发现 {len(duplicates)} 组重复照片描述")
            for date, file_path, count, max_id in duplicates:
                # 删除该组中除了最新一条之外的所有记录
                cursor.execute("""
                    DELETE FROM photo_descriptions
                    WHERE date = ? AND file_path = ? AND id != ?
                """, (date, file_path, max_id))
                print(f"    清理 {date} 的照片: {Path(file_path).name} (保留 id={max_id}, 删除 {count-1} 条)")
            conn.commit()
            print(f"  ✓ 照片描述去重完成")
        else:
            print("  ✓ 没有重复的照片描述")
    except Exception as e:
        print(f"  ✗ 清理照片描述失败: {e}")
    
    # 3. 显示清理后的统计
    try:
        cursor.execute("SELECT COUNT(*) FROM speech_records")
        speech_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM photo_descriptions")
        photo_count = cursor.fetchone()[0]
        print(f"  当前统计: {speech_count} 条语音记录, {photo_count} 条照片描述")
    except Exception as e:
        print(f"  统计失败: {e}")
    
    conn.close()

def main():
    """主函数"""
    data_dir = Path("/home/bob/projects/mumu/data")
    users_dir = data_dir / "users"
    
    if not users_dir.exists():
        print(f"用户目录不存在: {users_dir}")
        sys.exit(1)
    
    # 遍历所有用户数据库
    db_files = list(users_dir.glob("*.db"))
    print(f"找到 {len(db_files)} 个用户数据库")
    
    for db_path in db_files:
        cleanup_user_db(db_path)
    
    print("\n" + "="*50)
    print("数据清理完成！")
    print("="*50)

if __name__ == "__main__":
    main()
