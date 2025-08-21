import tkinter as tk
from tkinter import ttk, messagebox
import win32gui
import win32con
import win32api
import keyboard
import threading
import time
import ctypes
import sys
import os

def is_admin():
    """בדיקה אם האפליקציה רצה עם הרשאות מנהל"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    """הפעלה מחדש עם הרשאות מנהל"""
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, 1
        )
        return True
    except:
        return False

class BorderOverlay:
    """מחלקה ליצירת מסגרת אדומה סביב חלון"""
    
    def __init__(self, target_hwnd):
        self.target_hwnd = target_hwnd
        self.border_windows = []
        self.border_thickness = 10  # עובי גדול יותר לנראות טובה יותר
        self.border_color = 0x0000FF  # אדום בפורמט BGR
        self.is_visible = False
        
    def create_border_windows(self):
        """יצירת מסגרת אדומה סביב החלון"""
        try:
            if not win32gui.IsWindow(self.target_hwnd):
                return False
                
            # קבלת מיקום וגודל החלון
            rect = win32gui.GetWindowRect(self.target_hwnd)
            left, top, right, bottom = rect
            width = right - left
            height = bottom - top
            
            # יצירת 4 חלונות דקים שיוצרים מסגרת
            border_positions = [
                # עליון
                (left - self.border_thickness, top - self.border_thickness, 
                 width + 2 * self.border_thickness, self.border_thickness),
                # תחתון  
                (left - self.border_thickness, bottom,
                 width + 2 * self.border_thickness, self.border_thickness),
                # שמאל
                (left - self.border_thickness, top,
                 self.border_thickness, height),
                # ימין
                (right, top,
                 self.border_thickness, height)
            ]
            
            for x, y, w, h in border_positions:
                # יצירת חלון קטן אדום
                border_hwnd = win32gui.CreateWindowEx(
                    win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW,
                    "Static",  # שימוש במחלקה פשוטה
                    "",
                    win32con.WS_POPUP | win32con.SS_NOTIFY,
                    x, y, w, h,
                    None, None, None, None
                )
                
                if border_hwnd:
                    # הגדרת צבע אדום מוצק
                    win32gui.SetLayeredWindowAttributes(border_hwnd, self.border_color, 255, win32con.LWA_COLORKEY)
                    self.border_windows.append(border_hwnd)
            
            self.is_visible = True
            return True
                
        except Exception as e:
            print(f"שגיאה ביצירת מסגרת: {e}")
            return False
    
    def update_position(self):
        """עדכון מיקום המסגרת"""
        try:
            if not win32gui.IsWindow(self.target_hwnd) or not self.border_windows:
                return False
                
            rect = win32gui.GetWindowRect(self.target_hwnd)
            left, top, right, bottom = rect
            width = right - left
            height = bottom - top
            
            # מיקומים חדשים לגבולות
            new_positions = [
                # עליון
                (left - self.border_thickness, top - self.border_thickness, 
                 width + 2 * self.border_thickness, self.border_thickness),
                # תחתון
                (left - self.border_thickness, bottom,
                 width + 2 * self.border_thickness, self.border_thickness),
                # שמאל
                (left - self.border_thickness, top,
                 self.border_thickness, height),
                # ימין
                (right, top,
                 self.border_thickness, height)
            ]
            
            # עדכון מיקום כל חלון גבול
            for i, (border_hwnd, (x, y, w, h)) in enumerate(zip(self.border_windows, new_positions)):
                if win32gui.IsWindow(border_hwnd):
                    win32gui.SetWindowPos(border_hwnd, win32con.HWND_TOPMOST,
                                        x, y, w, h,
                                        win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
            
            return True
            
        except Exception as e:
            print(f"שגיאה בעדכון מיקום מסגרת: {e}")
            return False
    
    def hide_border(self):
        """הסתרת המסגרת"""
        try:
            for border_hwnd in self.border_windows:
                if win32gui.IsWindow(border_hwnd):
                    win32gui.ShowWindow(border_hwnd, win32con.SW_HIDE)
            self.is_visible = False
        except Exception as e:
            print(f"שגיאה בהסתרת מסגרת: {e}")
    
    def show_border(self):
        """הצגת המסגרת"""
        try:
            for border_hwnd in self.border_windows:
                if win32gui.IsWindow(border_hwnd):
                    win32gui.ShowWindow(border_hwnd, win32con.SW_SHOW)
                    win32gui.SetWindowPos(border_hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                        win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)
            self.is_visible = True
        except Exception as e:
            print(f"שגיאה בהצגת מסגרת: {e}")
    
    def destroy_border(self):
        """השמדת המסגרת"""
        try:
            for border_hwnd in self.border_windows:
                if win32gui.IsWindow(border_hwnd):
                    win32gui.DestroyWindow(border_hwnd)
            self.border_windows.clear()
            self.is_visible = False
        except Exception as e:
            print(f"שגיאה בהשמדת מסגרת: {e}")
    
    @staticmethod
    def border_wnd_proc(hwnd, msg, wparam, lparam):
        """פונקציית טיפול בהודעות עבור חלונות הגבול"""
        if msg == win32con.WM_PAINT:
            return 0
        elif msg == win32con.WM_ERASEBKGND:
            return 1
        elif msg == win32con.WM_LBUTTONDOWN:
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

class AlwaysOnTopApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("אפליקציית חלונות מעל תמיד עם מסגרות")
        self.root.geometry("500x450")
        self.root.attributes('-topmost', True)  # האפליקציה עצמה תמיד מעל
        
        # משתנים לניהול המצב
        self.hotkey = "ctrl+u"  # ברירת מחדל
        self.always_on_top_windows = []  # רשימת החלונות שמעל תמיד
        self.is_active = False
        self.hotkey_thread = None
        
        # משתנים למסגרות
        self.border_overlays = {}  # מיפוי בין hwnd למסגרת
        self.border_update_thread = None
        self.border_update_active = False
        
        # משתנים חדשים
        self.hide_console = True  # הסתרת חלון CMD
        self.auto_start = False  # הפעלה אוטומטית
        self.minimized_to_tray = False  # מזעור לשורת המשימות
        
        self.setup_ui()
        
    def setup_ui(self):
        # כותרת
        title_label = tk.Label(self.root, text="אפליקציית חלונות מעל תמיד", 
                              font=("Arial", 16, "bold"))
        title_label.pack(pady=10)
        
        # מסגרת להגדרת מקשי קיצור
        hotkey_frame = tk.Frame(self.root)
        hotkey_frame.pack(pady=10, padx=20, fill="x")
        
        tk.Label(hotkey_frame, text="שילוב מקשים:", font=("Arial", 12)).pack(anchor="w")
        
        key_selection_frame = tk.Frame(hotkey_frame)
        key_selection_frame.pack(fill="x", pady=5)
        
        # תיבת בחירה למקש ראשון
        self.key1_var = tk.StringVar(value="ctrl")
        key1_combo = ttk.Combobox(key_selection_frame, textvariable=self.key1_var, 
                                 values=["ctrl", "alt", "shift"], width=10, state="readonly")
        key1_combo.pack(side="left", padx=5)
        
        tk.Label(key_selection_frame, text="+").pack(side="left", padx=5)
        
        # תיבת בחירה למקש שני
        self.key2_var = tk.StringVar(value="u")
        key2_combo = ttk.Combobox(key_selection_frame, textvariable=self.key2_var,
                                 values=list("abcdefghijklmnopqrstuvwxyz"), width=5, state="readonly")
        key2_combo.pack(side="left", padx=5)
        
        # כפתור להחלת השילוב
        apply_btn = tk.Button(key_selection_frame, text="החל שילוב", 
                             command=self.apply_hotkey, bg="#4CAF50", fg="white")
        apply_btn.pack(side="left", padx=10)
        
        # מציג השילוב הנוכחי
        self.current_hotkey_label = tk.Label(hotkey_frame, text=f"שילוב נוכחי: {self.hotkey}", 
                                           font=("Arial", 10), fg="blue")
        self.current_hotkey_label.pack(anchor="w", pady=5)
        
        # מסגרת להגדרות מסגרת
        border_frame = tk.LabelFrame(self.root, text="הגדרות מסגרת חזותית", font=("Arial", 10))
        border_frame.pack(pady=5, padx=20, fill="x")
        
        # תיבת סימן להפעלת מסגרות
        self.show_borders_var = tk.BooleanVar(value=True)
        borders_check = tk.Checkbutton(border_frame, text="הצג מסגרת אדומה סביב חלונות פעילים",
                                      variable=self.show_borders_var, command=self.toggle_all_borders)
        borders_check.pack(anchor="w", padx=5, pady=2)
        
        # מידע על המסגרות
        info_label = tk.Label(border_frame, text="מסגרת אדומה דקה תוצג סביב כל חלון שנבחר להיות מעל תמיד",
                             font=("Arial", 8), fg="gray")
        info_label.pack(anchor="w", padx=5)
        
        # כפתורי הפעלה והפסקה
        control_frame = tk.Frame(self.root)
        control_frame.pack(pady=20)
        
        self.start_btn = tk.Button(control_frame, text="הפעל שירות", 
                                  command=self.start_service, bg="#2196F3", 
                                  fg="white", font=("Arial", 12), width=12)
        self.start_btn.pack(side="left", padx=10)
        
        self.stop_btn = tk.Button(control_frame, text="הפסק שירות", 
                                 command=self.stop_service, bg="#f44336", 
                                 fg="white", font=("Arial", 12), width=12)
        self.stop_btn.pack(side="left", padx=10)
        self.stop_btn.config(state="disabled")
        
        # מידע על מצב השירות
        self.status_label = tk.Label(self.root, text="השירות לא פעיל", 
                                    font=("Arial", 12, "bold"), fg="red")
        self.status_label.pack(pady=10)
        
        # רשימת חלונות פעילים
        list_frame = tk.Frame(self.root)
        list_frame.pack(pady=10, padx=20, fill="both", expand=True)
        
        tk.Label(list_frame, text="חלונות מעל תמיד:", font=("Arial", 11, "bold")).pack(anchor="w")
        
        # מסגרת עם scroll bar
        listbox_frame = tk.Frame(list_frame)
        listbox_frame.pack(fill="both", expand=True, pady=5)
        
        self.windows_listbox = tk.Listbox(listbox_frame, height=6, font=("Arial", 9))
        scrollbar = tk.Scrollbar(listbox_frame, orient="vertical", command=self.windows_listbox.yview)
        self.windows_listbox.config(yscrollcommand=scrollbar.set)
        
        self.windows_listbox.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # כפתורים לניהול
        buttons_frame = tk.Frame(list_frame)
        buttons_frame.pack(pady=5)
        
        clear_btn = tk.Button(buttons_frame, text="נקה הכל", 
                             command=self.clear_all_windows, bg="#FF9800", 
                             fg="white", width=10)
        clear_btn.pack(side="left", padx=5)
        
        refresh_btn = tk.Button(buttons_frame, text="רענן", 
                               command=self.update_windows_list, bg="#607D8B", 
                               fg="white", width=10)
        refresh_btn.pack(side="left", padx=5)
        
        # מסגרת הגדרות נוספות
        additional_settings_frame = tk.LabelFrame(self.root, text="הגדרות נוספות", font=("Arial", 10))
        additional_settings_frame.pack(pady=5, padx=20, fill="x")
        
        # תיבת סימן להסתרת חלון CMD
        self.hide_console_var = tk.BooleanVar(value=True)
        console_check = tk.Checkbutton(additional_settings_frame, text="הסתר חלון CMD",
                                      variable=self.hide_console_var, command=self.toggle_console_visibility)
        console_check.pack(anchor="w", padx=5, pady=2)
        
        # תיבת סימן להפעלה אוטומטית
        self.auto_start_var = tk.BooleanVar(value=False)
        autostart_check = tk.Checkbutton(additional_settings_frame, text="הפעל אוטומטית עם Windows",
                                        variable=self.auto_start_var, command=self.toggle_autostart)
        autostart_check.pack(anchor="w", padx=5, pady=2)
        
        # כפתור מזעור לשורת המשימות
        minimize_btn = tk.Button(additional_settings_frame, text="מזער לשורת המשימות", 
                                command=self.minimize_to_tray, bg="#9C27B0", fg="white", width=15)
        minimize_btn.pack(anchor="w", padx=5, pady=5)
        
        # הוראות שימוש
        instructions_frame = tk.Frame(self.root)
        instructions_frame.pack(pady=5, padx=20, fill="x")
        
        instructions = """הוראות: בחר שילוב מקשים → הפעל שירות → עבור לחלון הרצוי → לחץ על השילוב"""
        
        instructions_label = tk.Label(instructions_frame, text=instructions, 
                                     font=("Arial", 9), justify="center", 
                                     wraplength=450, bg="#f0f0f0", relief="solid", bd=1)
        instructions_label.pack(fill="x", pady=5)
        
    def apply_hotkey(self):
        """החלת שילוב המקשים החדש"""
        key1 = self.key1_var.get()
        key2 = self.key2_var.get()
        new_hotkey = f"{key1}+{key2}"
        
        if new_hotkey == self.hotkey:
            messagebox.showinfo("מידע", "השילוב כבר פעיל")
            return
            
        old_hotkey = self.hotkey
        self.hotkey = new_hotkey
        
        # עדכון התצוגה
        self.current_hotkey_label.config(text=f"שילוב נוכחי: {self.hotkey}")
        
        # אם השירות פעיל, הפסק והפעל מחדש
        if self.is_active:
            self.stop_service()
            time.sleep(0.5)
            self.start_service()
        
        messagebox.showinfo("הצלחה", f"שילוב המקשים שונה מ-{old_hotkey} ל-{self.hotkey}")
        
    def start_service(self):
        """הפעלת שירות המקשי קיצור"""
        try:
            if self.is_active:
                messagebox.showwarning("אזהרה", "השירות כבר פעיל")
                return
                
            self.is_active = True
            self.hotkey_thread = threading.Thread(target=self.hotkey_listener, daemon=True)
            self.hotkey_thread.start()
            
            # הפעלת הליך עדכון מסגרות
            self.start_border_update_thread()
            
            self.start_btn.config(state="disabled")
            self.stop_btn.config(state="normal")
            self.status_label.config(text=f"השירות פעיל - {self.hotkey}", fg="green")
            
            print(f"השירות הופעל עם שילוב: {self.hotkey}")
            
        except Exception as e:
            self.is_active = False
            messagebox.showerror("שגיאה", f"לא ניתן להפעיל את השירות: {str(e)}")
            
    def stop_service(self):
        """הפסקת שירות המקשי קיצור"""
        try:
            self.is_active = False
            keyboard.unhook_all()  # ביטול כל מקשי הקיצור
            
            # הפסקת הליך עדכון מסגרות
            self.stop_border_update_thread()
            
            # ביטול מצב "מעל תמיד" לכל החלונות
            for hwnd in self.always_on_top_windows.copy():
                try:
                    if win32gui.IsWindow(hwnd):
                        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                except:
                    continue
            
            # ניקוי כל המסגרות
            for overlay in list(self.border_overlays.values()):
                overlay.destroy_border()
            self.border_overlays.clear()
            
            # ניקוי רשימת החלונות
            self.always_on_top_windows.clear()
            
            self.start_btn.config(state="normal")
            self.stop_btn.config(state="disabled")
            self.status_label.config(text="השירות לא פעיל", fg="red")
            
            # עדכון הרשימה
            self.update_windows_list()
            
            print("השירות הופסק")
            
        except Exception as e:
            print(f"שגיאה בהפסקת השירות: {e}")
        
    def hotkey_listener(self):
        """האזנה למקשי קיצור"""
        try:
            print(f"מאזין למקשי קיצור: {self.hotkey}")
            keyboard.add_hotkey(self.hotkey, self.toggle_window_on_top)
            
            # לולאת המתנה
            while self.is_active:
                time.sleep(0.1)
                
        except Exception as e:
            print(f"שגיאה במאזין מקשים: {e}")
            self.is_active = False
    
    def start_border_update_thread(self):
        """הפעלת הליך עדכון מסגרות"""
        if not self.border_update_active:
            self.border_update_active = True
            self.border_update_thread = threading.Thread(target=self.border_update_loop, daemon=True)
            self.border_update_thread.start()
    
    def stop_border_update_thread(self):
        """הפסקת הליך עדכון מסגרות"""
        self.border_update_active = False
        
    def border_update_loop(self):
        """לולאת עדכון מיקום המסגרות"""
        while self.border_update_active:
            try:
                if self.show_borders_var.get():
                    for hwnd, overlay in list(self.border_overlays.items()):
                        if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                            overlay.update_position()
                        else:
                            # החלון נסגר או לא נראה - הסר את המסגרת
                            overlay.destroy_border()
                            del self.border_overlays[hwnd]
                            # הסר גם מרשימת החלונות הפעילים
                            if hwnd in self.always_on_top_windows:
                                self.always_on_top_windows.remove(hwnd)
                
                time.sleep(0.05)  # עדכון כל 50ms לתגובה מהירה יותר
                
            except Exception as e:
                print(f"שגיאה בלולאת עדכון מסגרות: {e}")
                time.sleep(1)
    
    def toggle_all_borders(self):
        """החלפת מצב הצגת כל המסגרות"""
        if self.show_borders_var.get():
            # הצגת כל המסגרות
            for overlay in self.border_overlays.values():
                overlay.show_border()
        else:
            # הסתרת כל המסגרות
            for overlay in self.border_overlays.values():
                overlay.hide_border()
            
            # אם ביטלנו את הצגת המסגרות, נוסיף מסגרות חדשות לחלונות פעילים
            if self.is_active:
                for hwnd in self.always_on_top_windows:
                    if hwnd not in self.border_overlays and win32gui.IsWindow(hwnd):
                        self.add_border_to_window(hwnd)
    
    def add_border_to_window(self, hwnd):
        """הוספת מסגרת לחלון"""
        try:
            if hwnd not in self.border_overlays and self.show_borders_var.get():
                overlay = BorderOverlay(hwnd)
                if overlay.create_border_windows():
                    self.border_overlays[hwnd] = overlay
                    print(f"נוצרה מסגרת עבור חלון: {win32gui.GetWindowText(hwnd)}")
                    # הצגת המסגרת מיד
                    overlay.show_border()
        except Exception as e:
            print(f"שגיאה בהוספת מסגרת: {e}")
    
    def remove_border_from_window(self, hwnd):
        """הסרת מסגרת מחלון"""
        try:
            if hwnd in self.border_overlays:
                self.border_overlays[hwnd].destroy_border()
                del self.border_overlays[hwnd]
                print(f"הוסרה מסגרת מחלון: {win32gui.GetWindowText(hwnd)}")
        except Exception as e:
            print(f"שגיאה בהסרת מסגרת: {e}")
            
    def toggle_window_on_top(self):
        """החלפת מצב החלון הפעיל בין מעל תמיד לרגיל"""
        try:
            # קבלת החלון הפעיל כעת
            hwnd = win32gui.GetForegroundWindow()
            if hwnd == 0:
                print("לא נמצא חלון פעיל")
                return
                
            # בדיקה שזה לא החלון של האפליקציה עצמה
            app_hwnd = self.root.winfo_id()
            if hwnd == app_hwnd:
                print("לא ניתן להחיל על חלון האפליקציה עצמה")
                return
                
            window_title = win32gui.GetWindowText(hwnd)
            if not window_title.strip():
                window_title = "חלון ללא שם"
            
            # בדיקה אם החלון כבר ברשימה
            if hwnd in self.always_on_top_windows:
                # ביטול מצב "מעל תמיד" והסרת מסגרת
                win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                self.always_on_top_windows.remove(hwnd)
                self.remove_border_from_window(hwnd)
                print(f"בוטל מצב 'מעל תמיד' והוסרה מסגרת עבור: {window_title}")
            else:
                # הפעלת מצב "מעל תמיד" והוספת מסגרת
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                                    win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                self.always_on_top_windows.append(hwnd)
                self.add_border_to_window(hwnd)
                print(f"הופעל מצב 'מעל תמיד' ונוספה מסגרת עבור: {window_title}")
                
            # עדכון הרשימה
            self.root.after(100, self.update_windows_list)
                
        except Exception as e:
            print(f"שגיאה בהחלפת מצב החלון: {e}")
            
    def update_windows_list(self):
        """עדכון רשימת החלונות בממשק"""
        try:
            self.windows_listbox.delete(0, tk.END)
            
            # הסרת חלונות שכבר לא קיימים
            valid_windows = []
            for hwnd in self.always_on_top_windows:
                try:
                    if win32gui.IsWindow(hwnd) and win32gui.IsWindowVisible(hwnd):
                        window_title = win32gui.GetWindowText(hwnd)
                        if not window_title.strip():
                            window_title = f"חלון ללא שם (ID: {hwnd})"
                        self.windows_listbox.insert(tk.END, f"🔴 {window_title}")
                        valid_windows.append(hwnd)
                    else:
                        # החלון לא קיים או לא נראה - הסר את המסגרת
                        if hwnd in self.border_overlays:
                            self.border_overlays[hwnd].destroy_border()
                            del self.border_overlays[hwnd]
                except Exception as e:
                    print(f"שגיאה בבדיקת חלון {hwnd}: {e}")
                    continue
                    
            self.always_on_top_windows = valid_windows
            
            # הצגת מספר החלונות
            count = len(valid_windows)
            if count == 0:
                self.windows_listbox.insert(tk.END, "אין חלונות פעילים")
            
        except Exception as e:
            print(f"שגיאה בעדכון רשימת החלונות: {e}")
    
    def toggle_console_visibility(self):
        """החלפת מצב הצגת חלון CMD"""
        try:
            if self.hide_console_var.get():
                # הסתרת חלון CMD
                import subprocess
                subprocess.run(['cmd', '/c', 'title', 'Hidden Console'], shell=True)
                # הסתרת חלון הנוכחי
                hwnd = win32gui.GetForegroundWindow()
                if "cmd" in win32gui.GetWindowText(hwnd).lower():
                    win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
            else:
                # הצגת חלון CMD
                hwnd = win32gui.GetForegroundWindow()
                if "cmd" in win32gui.GetWindowText(hwnd).lower():
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
        except Exception as e:
            print(f"שגיאה בהחלפת מצב חלון CMD: {e}")
    
    def toggle_autostart(self):
        """החלפת מצב הפעלה אוטומטית"""
        try:
            import winreg
            key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            app_name = "AlwaysOnTopApp"
            script_path = sys.argv[0]
            
            if self.auto_start_var.get():
                # הוספה להפעלה אוטומטית
                try:
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE)
                    winreg.SetValueEx(key, app_name, 0, winreg.REG_SZ, script_path)
                    winreg.CloseKey(key)
                    messagebox.showinfo("הצלחה", "האפליקציה תופעל אוטומטית עם Windows")
                except Exception as e:
                    messagebox.showerror("שגיאה", f"לא ניתן להוסיף להפעלה אוטומטית: {e}")
                    self.auto_start_var.set(False)
            else:
                # הסרה מהפעלה אוטומטית
                try:
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_WRITE)
                    winreg.DeleteValue(key, app_name)
                    winreg.CloseKey(key)
                    messagebox.showinfo("הצלחה", "האפליקציה הוסרה מהפעלה אוטומטית")
                except Exception as e:
                    messagebox.showerror("שגיאה", f"לא ניתן להסיר מהפעלה אוטומטית: {e}")
                    self.auto_start_var.set(True)
        except Exception as e:
            print(f"שגיאה בהחלפת מצב הפעלה אוטומטית: {e}")
    
    def minimize_to_tray(self):
        """מזעור לשורת המשימות עם אייקון גלגל שיניים אדום"""
        try:
            self.root.withdraw()  # הסתרת החלון הראשי
            self.minimized_to_tray = True
            
            # הסתרת חלון CMD/Python
            self.hide_console_window()
            
            # יצירת חלון קטן לשורת המשימות עם אייקון גלגל שיניים
            self.tray_window = tk.Toplevel()
            self.tray_window.title("Always On Top")
            self.tray_window.geometry("150x80")
            self.tray_window.attributes('-topmost', True)
            self.tray_window.protocol("WM_DELETE_WINDOW", self.restore_from_tray)
            
            # הגדרת אייקון גלגל שיניים אדום
            self.tray_window.iconbitmap(default="")  # הסרת אייקון ברירת מחדל
            
            # מסגרת עם רקע אדום
            frame = tk.Frame(self.tray_window, bg="red", relief="raised", bd=2)
            frame.pack(fill="both", expand=True, padx=5, pady=5)
            
            # אייקון גלגל שיניים (טקסט)
            gear_label = tk.Label(frame, text="⚙️", font=("Arial", 24), bg="red", fg="white")
            gear_label.pack(pady=5)
            
            # כפתור להחזרת החלון
            restore_btn = tk.Button(frame, text="החזר", 
                                   command=self.restore_from_tray, bg="white", fg="red",
                                   font=("Arial", 8), width=8)
            restore_btn.pack(pady=2)
            
            # הצגת הודעה קצרה
            print("האפליקציה מזערה לשורת המשימות עם אייקון גלגל שיניים אדום")
            
        except Exception as e:
            print(f"שגיאה במזעור לשורת המשימות: {e}")
    
    def restore_from_tray(self):
        """החזרת החלון מהשורת המשימות"""
        try:
            if hasattr(self, 'tray_window'):
                self.tray_window.destroy()
            self.root.deiconify()  # הצגת החלון
            self.minimized_to_tray = False
            print("החלון הוחזר מהשורת המשימות")
        except Exception as e:
            print(f"שגיאה בהחזרת החלון: {e}")
        
    def clear_all_windows(self):
        """ביטול מצב 'מעל תמיד' והסרת מסגרות לכל החלונות"""
        try:
            if not self.always_on_top_windows:
                messagebox.showinfo("מידע", "אין חלונות לניקוי")
                return
                
            count = 0
            for hwnd in self.always_on_top_windows.copy():
                try:
                    if win32gui.IsWindow(hwnd):
                        # ביטול מצב "מעל תמיד"
                        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                        # הסרת מסגרת
                        self.remove_border_from_window(hwnd)
                        count += 1
                except Exception as e:
                    print(f"שגיאה בביטול חלון {hwnd}: {e}")
                    continue
                    
            self.always_on_top_windows.clear()
            self.update_windows_list()
            messagebox.showinfo("הצלחה", f"{count} חלונות בוטלו ממצב 'מעל תמיד' והוסרו המסגרות")
            
        except Exception as e:
            messagebox.showerror("שגיאה", f"שגיאה בניקוי החלונות: {str(e)}")
        
    def run(self):
        """הפעלת האפליקציה"""
        def on_closing():
            """טיפול בסגירת האפליקציה"""
            try:
                if self.is_active:
                    self.stop_service()
                    time.sleep(0.2)
                
                # ביטול כל החלונות והסרת מסגרות
                for hwnd in self.always_on_top_windows.copy():
                    try:
                        if win32gui.IsWindow(hwnd):
                            win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                                                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)
                    except:
                        continue
                
                # ניקוי כל המסגרות
                for overlay in list(self.border_overlays.values()):
                    overlay.destroy_border()
                
                self.root.destroy()
                
            except Exception as e:
                print(f"שגיאה בסגירת האפליקציה: {e}")
                self.root.destroy()
        
        # הסתרת חלון CMD אוטומטית
        self.hide_console_window()
            
        self.root.protocol("WM_DELETE_WINDOW", on_closing)
        
        # הודעת התחלה
        print("האפליקציה עם מסגרות אדומות הופעלה בהצלחה!")
        print(f"שילוב מקשים ברירת מחדל: {self.hotkey}")
        
        self.root.mainloop()
    
    def hide_console_window(self):
        """הסתרת חלון CMD/Python"""
        try:
            # מציאת חלונות CMD/Python
            def enum_windows_callback(hwnd, windows):
                if win32gui.IsWindowVisible(hwnd):
                    window_text = win32gui.GetWindowText(hwnd)
                    # חיפוש חלונות CMD, Python, PowerShell
                    if any(keyword in window_text.lower() for keyword in ["cmd", "command", "python", "powershell", "terminal"]):
                        windows.append(hwnd)
                return True
            
            console_windows = []
            win32gui.EnumWindows(enum_windows_callback, console_windows)
            
            # הסתרת חלונות CMD/Python
            for hwnd in console_windows:
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
                    print(f"הוסתר חלון: {win32gui.GetWindowText(hwnd)}")
                except:
                    continue
                
        except Exception as e:
            print(f"שגיאה בהסתרת חלון CMD: {e}")

if __name__ == "__main__":
    try:
        # הפעלת האפליקציה ישירות ללא בדיקת הרשאות מנהל
        print("מפעיל את האפליקציה...")
        app = AlwaysOnTopApp()
        app.run()
        
    except ImportError as e:
        error_msg = f"""
שגיאה: חסרות ספריות נדרשות.

להתקנה, הפעל בטרמינל:
pip install pywin32 keyboard

שגיאה מפורטת: {str(e)}
        """
        print(error_msg)
        try:
            temp_root = tk.Tk()
            temp_root.withdraw()
            messagebox.showerror("שגיאת התקנה", error_msg)
            temp_root.destroy()
        except:
            pass
            
    except Exception as e:
        error_msg = f"שגיאה בהפעלת האפליקציה: {str(e)}"
        print(error_msg)
        try:
            temp_root = tk.Tk()
            temp_root.withdraw()
            messagebox.showerror("שגיאה", error_msg)
            temp_root.destroy()
        except:
            pass
        
    finally:
        # ניקוי סופי
        try:
            keyboard.unhook_all()
            print("ניקוי משאבים הושלם")
        except:
            pass

print("🔴 אפליקציה עם מסגרות אדומות מוכנה לשימוש!")
            