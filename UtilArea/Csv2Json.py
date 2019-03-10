# -*- coding: utf-8 -*-
# @Author  : Lone Ranger
# @Function :将原始的csv文件转为json文件存储，用作输入

import json

import pandas as pd


class Csv2Json():
    def __init__(self, read_path, write_path):
        self.read_path = read_path
        self.write_path = write_path

    def read_csv(self):
        dataframe = pd.read_csv(self.read_path, encoding='utf-8')
        print('\t'.join(dataframe.columns))
        dataframe = dataframe.reset_index()
        new_df = dataframe.drop(axis=1, columns=[dataframe.columns[1]], inplace=False)
        # print(dataframe)
        dictionary = new_df.to_dict(orient='records')
        return dictionary

    def write_json(self, dic):
        try:
            with open(self.write_path, 'w', encoding='utf-8') as f:
                for content in dic:
                    json_data = json.dumps(content, ensure_ascii=False)
                    f.write(json_data)
                    f.write('\n')
        except (Exception) as e:
            print(e)