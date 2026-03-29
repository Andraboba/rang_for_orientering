import tkinter as tk
from tkinter import messagebox
import requests
import os

def save_html_to_txt():
    url = url_entry.get()
    sfr = int(sfr_entry.get())

    response = requests.get(url)

    if response.status_code == 200:
        text = response.content.decode('utf-8') if sfr == 1 else response.content.decode('windows-1251')

        with open('temp_data.txt', 'w', encoding='utf-8') as file:
            file.write(text)
        messagebox.showinfo("Success", f"Data successfully saved to {'temp_data.txt'}")
    else:
        messagebox.showerror("Error", "Failed to retrieve data from the web page")

def save_for_teem(result,base,m,dlin,f,fot):
    if str([m[1], m[2]]) not in base:
        base.append([m[1], m[2]])
    for i in range(len(dlin)):
        if i == len(dlin) - 1:
            break
        else:
            if dlin[i] < f < dlin[i + 1]:
                if m[-1] == "3.13.12.2" or m[-1] == "п.3.13.12.3" or m[-1] == "п.3.13.12.2" or m[-1] == "п.п.7.2.6":
                    fot.append(
                        f"снят,для справки место {m[0]} из {dlin[i + 1] - dlin[i] - 1} {m[1]}, {m[2]}, очки:{result}")
                else:
                    fot.append(f"{m[-1]} из {dlin[i + 1] - dlin[i] - 1} , {m[1]} {m[2]}, очки:{result}")
    return fot



def extract_data_from_txt():

    sfr = int(sfr_entry.get())

    try:
        with open("temp_data.txt", 'r', encoding='utf-8') as file:
            if sfr == 0:
                results = [1]
                for y in file:
                    if y[0] != " " and "h2" not in y:
                        continue
                    else:
                        results.append(y)
                return results
            if sfr == 1:
                results = [0]
                fil1 = [y for y in file]
                for y in fil1:
                    if "<tr" not in y and "<a" not in y:
                        continue
                    else:
                        results.append(y)
                return results
    except FileNotFoundError:
        messagebox.showerror("Error", "File not found")


def process_data():
    team_name = teamname_entry.get()
    team_results = extract_data_from_txt()
    dlin = []
    fot = []
    try:
        with open("base.txt") as f1:
            base = [y.replace("\n", "") for y in f1]
    except IOError as e:
        with open("base.txt","w") as f1:
            base=[]
    if team_results:
        if team_results[0] == 1:
            team_results.pop(0)
            for i in team_results:
                if "h2" in i:
                    dlin.append(team_results.index(i))
            dlin.append(len(team_results))
            for y in team_results:
                g = y.replace("\n", "")
                m = list(map(str, g.split(" ")))
                m = list(filter(None, m))
                if len(m[-3])!=4:
                    m.pop(-2)
                if m[0] == "1":
                    tlid = int(m[-2][0] + m[-2][1]) * 3600 + int(m[-2][3] + m[-2][4]) * 60 + int(m[-2][6] + m[-2][6])
                if team_name in m or str([m[1], m[2]]) in base:
                    if m[-1] == "3.13.12.2" or m[-1] == "п.3.13.12.3" or m[-1] == "п.3.13.12.2" or m[-1] == "п.п.7.2.6":
                        result = 0
                    else:
                        tlich = int(m[-2][0] + m[-2][1]) * 3600 + int(m[-2][3] + m[-2][4]) * 60 + int( m[-2][6] + m[-2][6])
                        result = int((2 - (tlich / tlid)) * 1000)
                        if result < 0:
                            result = 1
                    f = team_results.index(y)
                    fot=save_for_teem(result, base, m, dlin, f, fot)



            with open(output_filename_entry.get(), "w") as file:
                for item in fot:
                    file.write(str(item) + "\n")
                os.remove("base.txt")
                with open('base.txt',"w") as f2:
                    for item in base:
                        f2.write(str(item) + "\n")

                messagebox.showinfo("Успех", "Данные успешно обработаны и сохранены")

        elif team_results[0] == 0:
            team_results.pop(0)
            q = []
            for i in team_results:
                if "<a href" in i:
                    dlin.append(team_results.index(i))

                q.append(i)
            dlin.append(len(team_results))

            for y in q:
                g = y.replace("\n", "")
                g = g.replace("<tr style='background: #FFFFFF;'><td><nobr>", " ")
                g = g.replace("</td><td><nobr>", " ")
                g = g.replace("</td><td class = 'cr'><nobr>", " ")
                g = g.replace("</td></tr", " ")
                g = g.replace("<tr  class = 'yl'><td><nobr>", " ")
                m = list(map(str, g.split(" ")))
                m = list(filter(None, m))
                if len(m)>4:
                    if team_name in m or str([m[2],m[3]]) in base:
                        if "cнят" not in m:
                            s=[y for y in range(len(m)) if m[y] == m[0]]
                            if len(s)==2:
                                if m[0]=="1":
                                    result=1000
                                else:
                                    tlich= int(m[int(s[1] - 1)][0] + m[int(s[1] - 1)][1]) * 3600 + int(m[int(s[1] - 1)][3] + m[int(s[1] - 1)][4]) * 60 + int(m[int(s[1] - 1)][6] + (m[int(s[1] - 1)][7]))
                                    if len(m[int(s[1] + 1)])==5:
                                        tlid = int(m[int(s[1] - 1)][0] + m[int(s[1] - 1)][1]) * 3600 + int(m[int(s[1] - 1)][3] + m[int(s[1] - 1)][4]) * 60 + int(m[int(s[1] - 1)][6] + (m[int(s[1] - 1)][7])) - int(m[int(s[1] + 1)][1])*60 - int(m[int(s[1] + 1)][3] + (m[int(s[1] + 1)][4]))
                                    else:
                                        tlid = int(m[int(s[1] - 1)][0] + m[int(s[1] - 1)][1]) * 3600 + int(m[int(s[1] - 1)][3] + m[int(s[1] - 1)][4]) * 60 + int(m[int(s[1] - 1)][6] + (m[int(s[1] - 1)][7])) - int(m[int(s[1] + 1)][1] + m[int(s[1] + 1)][2]) * 60 - int(m[int(s[1] + 1)][4] + (m[int(s[1] + 1)][5]))
                                    result = int((2 - (tlich/tlid))*1000)
                                if result<0:
                                    result=1
                        else:
                            result=0
                        f = team_results.index(y)
                        if str([m[2],m[3]]) not in base:
                            base.append([m[2],m[3]])
                        for i in range(len(dlin)):
                            if i == len(dlin) - 1:
                                break
                            else:
                                if dlin[i] < f < dlin[i + 1]:
                                    if "cнят" in m:
                                        fot.append(
                                            f"снят,для справки место {m[0]} из {dlin[i + 1] - dlin[i] - 1} {m[2]}, {m[3]}, очки:{result}")
                                    else:
                                        fot.append(f"{m[0]} из {dlin[i + 1] - dlin[i] - 2} , {m[2]} {m[3]}, очки:{result}")
            with open(output_filename_entry.get(), "w") as file:
                for item in fot:
                    file.write(str(item) + "\n")
                os.remove("base.txt")
                with open('base.txt', "w") as f2:
                    for item in base:
                        f2.write(str(item) + "\n")
                messagebox.showinfo("Успех", "Данные успешно обработаны и сохранены")

    else:
        messagebox.showerror("Error", "Failed to retrieve team results")
def paste_from_clipboard(entry):
    try:
        clipboard_text = root.clipboard_get()
        entry.insert(tk.END, clipboard_text)
    except tk.TclError:
        pass

# Create main window
root = tk.Tk()
root.title("Data Extraction")

# Create and place widgets
url_label = tk.Label(root, text="Ссылка на результаты:")
url_label.grid(row=0, column=0, sticky="w")

url_entry = tk.Entry(root, width=50)
url_entry.grid(row=0, column=1)

paste_url_button = tk.Button(root, text="Вставить", command=lambda: paste_from_clipboard(url_entry))
paste_url_button.grid(row=0, column=2)

save_button = tk.Button(root, text="Сохранить данные", command=save_html_to_txt)
save_button.grid(row=1, column=0, columnspan=2, pady=5)

teamname_label = tk.Label(root, text="Название команды:")
teamname_label.grid(row=2, column=0, sticky="w")

teamname_entry = tk.Entry(root, width=50)
teamname_entry.grid(row=2, column=1)

paste_teamname_button = tk.Button(root, text="Вставить", command=lambda: paste_from_clipboard(teamname_entry))
paste_teamname_button.grid(row=2, column=2)

process_button = tk.Button(root, text="Обработать данные", command=process_data)
process_button.grid(row=3, column=0, columnspan=2, pady=5)

output_filename_label = tk.Label(root, text="Путь для сохранения обработанного файла:")
output_filename_label.grid(row=4, column=0, sticky="w")

output_filename_entry = tk.Entry(root, width=50)
output_filename_entry.grid(row=4, column=1)

paste_output_filename_button = tk.Button(root, text="Вставить", command=lambda: paste_from_clipboard(output_filename_entry))
paste_output_filename_button.grid(row=4, column=2)

teamname_label = tk.Label(root, text="Если sfr то ввести 1 иначе 0")
teamname_label.grid(row=5, column=0, sticky="w")

sfr_entry = tk.Entry(root, width=50)
sfr_entry.grid(row=5, column=1)
# Run the main event loop
root.mainloop()
