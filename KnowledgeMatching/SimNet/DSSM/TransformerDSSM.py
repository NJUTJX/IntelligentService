# coding=utf-8

"""
author: 王黎成
function: 通过使用双向GRU+Transformer作为表示层进行语义相似度计算
"""

# 引入外部库
import os
import numpy as np
import tensorflow as tf
from tensorflow.contrib.rnn import GRUCell, DropoutWrapper


# 引入内部库
from UtilArea.Sampling import *


class TransformerDSSM:
	def __init__ (self, q_set=None,  # 问题集,二维数组
	              t_set=None,  # 答案集,二维数组
	              dict_set=None, # 字典集，[词：index]
	              vec_set=None,  # 向量集，[向量]，与dict_set顺序一致
	              batch_size=None, # 训练批次，默认是全部数据
	              hidden_num=256,  # 隐藏层个数
	              attention_num = 512, # 注意力机制的数目
	              learning_rate=0.0001,  # 学习率
	              epoch_steps=10,  # 训练迭代次数
	              gamma=20, # 余弦相似度平滑因子
	              is_train=True,  # 是否进行训练
	              is_extract = False, # 是否进行t特征提取
	              is_sample = False # 是否采用随机采样方法进行训练
				  ):
		# 外部参数
		self.q_set = q_set
		self.t_set = t_set
		self.dict_set = dict_set
		self.vec_set = vec_set
		# 最后一行表示字典中没有词的词向量,正态分布初始化
		self.vec_set.append(list(np.random.randn(len(self.vec_set[0]))))
		self.batch_size = batch_size
		self.hidden_num = hidden_num
		self.attention_num = attention_num
		self.learning_rate = learning_rate
		self.epoch_steps = epoch_steps
		self.gamma = gamma
		self.is_train = is_train
		self.is_extract = is_extract
		self.is_sample = is_sample

		# 内部参数
		self.q_size = 0
		self.t_size = 0 # 即可以为常数，也可以为占位符
		self.negative_sample_num = 0
		self.q_actual_length = []
		self.t_actual_length = []
		self.q_max_length = 0
		self.t_max_length = 0
		self.model_save_name = './ModelMemory/SimNet/DSSM/TransformerDSSM/model/transformerDSSM'
		self.model_save_checkpoint = './ModelMemory/SimNet/DSSM/TransformerDSSM/model/checkpoint'

		# 模型参数
		self.graph = None
		self.session = None
		self.saver = None
		self.q_inputs = None
		self.q_inputs_actual_length = None
		self.t_inputs = None
		self.t_inputs_actual_length = None
		self.t_final_state = None
		self.top_k_answer = None
		self.outputs_prob = None
		self.outputs_index = None
		self.accuracy = None
		self.loss = None
		self.train_op = None

		# 归一化层构建
		self.layer_normalization = LayerNormalization(self.hidden_num * 2)

	def init_model_parameters (self):
		print('Initializing------')
		if not self.is_extract:
			# 获取问题数据大小
			self.q_size = len(self.q_set)

			if self.batch_size is None and self.is_train:
				self.batch_size = self.q_size

			self.negative_sample_num = self.batch_size // 10

		# 获取答案数据大小
		if self.is_train:
			self.t_size = len(self.t_set)

		if not self.is_extract:
			# 获取q_set实际长度及最大长度
			self.q_actual_length = []
			for data in self.q_set:
				self.q_actual_length.append(len(data))
			self.q_max_length = max(self.q_actual_length)
			print('the max length of q set is %d' % self.q_max_length)

			# q_set数据补全
			for i in range(len(self.q_set)):
				if len(self.q_set[i]) < self.q_max_length:
					self.q_set[i] = self.q_set[i] + ['UNK' for _ in range(self.q_max_length - len(self.q_set[i]))]

		if self.is_train:
			# 获取t_set实际长度及最大长度
			for data in self.t_set:
				self.t_actual_length.append(len(data))
			self.t_max_length = max(self.t_actual_length)
			print('the max length of t set is %d' % self.t_max_length)

			# t_set数据补全
			for i in range(len(self.t_set)):
				if len(self.t_set[i]) < self.t_max_length:
					self.t_set[i] = self.t_set[i] + ['UNK' for _ in range(self.t_max_length - len(self.t_set[i]))]

		pass

	def generate_data_set (self):
		if not self.is_extract:
			# 将q_set每一个字转换为其在字典中的序号
			for i in range(len(self.q_set)):
				for j in range(len(self.q_set[i])):
					if self.q_set[i][j] in self.dict_set:
						self.q_set[i][j] = self.dict_set[self.q_set[i][j]]
					else:
						self.q_set[i][j] = len(self.vec_set) - 1
			self.q_set = np.array(self.q_set)

		if self.is_train:
			# 将t_set每一个字转换为其在字典中的向量
			for i in range(len(self.t_set)):
				for j in range(len(self.t_set[i])):
					if self.t_set[i][j] in self.dict_set:
						self.t_set[i][j] = self.dict_set[self.t_set[i][j]]
					else:
						self.t_set[i][j] = len(self.vec_set) - 1
			self.t_set = np.array(self.t_set)

		pass

	def presentation_transformer (self, inputs, inputs_actual_length, reuse=None):
		with tf.variable_scope('presentation_layer', reuse=reuse):
			with tf.name_scope('structure_presentation_layer'):
				# 正向
				fw_cell = GRUCell(num_units=self.hidden_num)
				fw_drop_cell = DropoutWrapper(fw_cell, output_keep_prob=0.8)
				# 反向
				bw_cell = GRUCell(num_units=self.hidden_num)
				bw_drop_cell = DropoutWrapper(bw_cell, output_keep_prob=0.8)

				# 动态rnn函数传入的是一个三维张量，[batch_size,n_steps,n_input]  输出是一个元组 每一个元素也是这种形状
				if self.is_train and not self.is_extract:
					output, _ = tf.nn.bidirectional_dynamic_rnn(cell_fw=fw_drop_cell, cell_bw=bw_drop_cell,
					                                            inputs=inputs, sequence_length=inputs_actual_length,
					                                            dtype=tf.float32)
				else:
					output, _ = tf.nn.bidirectional_dynamic_rnn(cell_fw=fw_cell, cell_bw=bw_cell, inputs=inputs,
					                                            sequence_length=inputs_actual_length, dtype=tf.float32)

				# hiddens的长度为2，其中每一个元素代表一个方向的隐藏状态序列，将每一时刻的输出合并成一个输出
				structure_output = tf.concat(output, axis=2)

			with tf.name_scope('self_attention_layer'):
				# q, k, v define
				q_dense_layer = tf.layers.Dense(self.hidden_num * 2, use_bias=False, name="q")
				k_dense_layer = tf.layers.Dense(self.hidden_num * 2, use_bias=False, name="k")
				v_dense_layer = tf.layers.Dense(self.hidden_num * 2, use_bias=False, name="v")

				q = q_dense_layer(structure_output) * (self.hidden_num ** -0.5)
				k = k_dense_layer(structure_output)
				v = v_dense_layer(structure_output)

				# calculate
				logits = tf.matmul(q, k, transpose_b=True)
				weights = tf.nn.softmax(logits, name="attention_weights")
				if self.is_train and not self.is_extract:
					weights = tf.nn.dropout(weights, 0.8)
				self_attention_output = tf.matmul(weights, v)

				# skip-connect + LN
				self_attention_output += structure_output
				self_attention_output = self.layer_normalization(self_attention_output)

			with tf.name_scope('full_connect_layer'):
				filter_dense_layer = tf.layers.Dense(self.hidden_num * 2, use_bias=True, activation=tf.nn.relu,
					name="filter_layer")
				output_dense_layer = tf.layers.Dense(self.hidden_num * 2, use_bias=True, name="output_layer")

				# calculate
				full_connect_output = filter_dense_layer(self_attention_output)
				if self.is_train and not self.is_extract:
					full_connect_output = tf.nn.dropout(full_connect_output, 0.8)
				full_connect_output = output_dense_layer(full_connect_output)

				# skip-connect + LN
				full_connect_output += self_attention_output
				full_connect_output = self.layer_normalization(full_connect_output)

			with tf.name_scope('global_attention_layer'):
				w_omega = tf.Variable(tf.random_normal([self.hidden_num * 2, self.attention_num], stddev=0.1))
				b_omega = tf.Variable(tf.random_normal([self.attention_num], stddev=0.1))
				u_omega = tf.Variable(tf.random_normal([self.attention_num], stddev=0.1))

				v = tf.tanh(tf.tensordot(full_connect_output, w_omega, axes=1) + b_omega)

				vu = tf.tensordot(v, u_omega, axes=1, name='vu')  # (B,T) shape
				alphas = tf.nn.softmax(vu, name='alphas')  # (B,T) shape

				# tf.expand_dims用于在指定维度增加一维
				global_attention_output = tf.reduce_sum(structure_output * tf.expand_dims(alphas, -1), 1)

		return global_attention_output

	def matching_layer_training (self, q_final_state, t_final_state):
		with tf.name_scope('TrainProgress'):
			# 负采样
			t_temp_state = tf.tile(t_final_state, [1, 1])
			for i in range(self.negative_sample_num):
				rand = int((random.random() + i) * self.batch_size / self.negative_sample_num)
				t_final_state = tf.concat((t_final_state,
				                           tf.slice(t_temp_state, [rand, 0], [self.batch_size - rand, -1]),
				                           tf.slice(t_temp_state, [0, 0], [rand, -1])), 0)

			# ||q|| * ||t||
			q_norm = tf.tile(tf.sqrt(tf.reduce_sum(tf.square(q_final_state), 1, True)),
			                 [self.negative_sample_num + 1, 1])
			t_norm = tf.sqrt(tf.reduce_sum(tf.square(t_final_state), 1, True))
			norm_prod = tf.multiply(q_norm, t_norm)

			# q * tT
			prod = tf.reduce_sum(tf.multiply(tf.tile(q_final_state, [self.negative_sample_num + 1, 1]), t_final_state),
			                     1, True)

			# cosine
			cos_sim_raw = tf.truediv(prod, norm_prod)
			cos_sim = tf.transpose(
				tf.reshape(tf.transpose(cos_sim_raw), [self.negative_sample_num + 1, self.batch_size])) * self.gamma

		return cos_sim

	def matching_layer_infer (self, q_final_state, t_final_state):
		with tf.name_scope('InferProgress'):
			# ||q|| * ||t||
			q_sqrt = tf.sqrt(tf.reduce_sum(tf.square(q_final_state), 1, True))
			t_sqrt = tf.sqrt(tf.reduce_sum(tf.square(t_final_state), 1, True))
			norm_prod = tf.matmul(q_sqrt, t_sqrt, transpose_b=True)

			# q * tT
			prod = tf.matmul(q_final_state, t_final_state, transpose_b=True)

			# cosine
			cos_sim = tf.truediv(prod, norm_prod) * self.gamma

		return cos_sim

	def build_graph (self):
		# 构建模型训练所需的数据流图
		self.graph = tf.Graph()
		with self.graph.as_default():
			with tf.name_scope('placeholder'):
				# 定义Q输入
				if not self.is_extract:
					self.q_inputs = tf.placeholder(dtype=tf.int64, shape=[None, None])
					self.q_inputs_actual_length = tf.placeholder(dtype=tf.int32, shape=[None])

				# 定义T输入
				if self.is_train:
					self.t_inputs = tf.placeholder(dtype=tf.int64, shape=[None, self.t_max_length])
					self.t_inputs_actual_length = tf.placeholder(dtype=tf.int32, shape=[None])

			with tf.name_scope('InputLayer'):
				# 定义词向量
				embeddings = tf.constant(self.vec_set)

				# 将句子中的每个字转换为字向量
				if not self.is_extract:
					q_embeddings = tf.nn.embedding_lookup(embeddings, self.q_inputs)
				if self.is_train:
					t_embeddings = tf.nn.embedding_lookup(embeddings, self.t_inputs)

			with tf.name_scope('PresentationLayer'):
				if not self.is_extract:
					q_final_state = self.presentation_transformer(q_embeddings, self.q_inputs_actual_length)
				if self.is_train and self.is_extract:
					self.t_final_state = self.presentation_transformer(t_embeddings, self.t_inputs_actual_length)
				elif self.is_train:
					self.t_final_state = self.presentation_transformer(t_embeddings, self.t_inputs_actual_length,
					                                                    reuse=True)
				else:
					self.t_final_state = tf.placeholder(dtype=tf.float32, shape=[None, self.hidden_num * 2])
					self.t_size = tf.placeholder(dtype=tf.int32)
					self.batch_size = tf.placeholder(dtype=tf.int64)

			if not self.is_extract:
				with tf.name_scope('MatchingLayer'):
					if self.is_train:
						cos_sim = self.matching_layer_training(q_final_state, self.t_final_state)
					else:
						cos_sim = self.matching_layer_infer(q_final_state, self.t_final_state)

				# softmax归一化并输出
				prob = tf.nn.softmax(cos_sim)
				if not self.is_train:
					self.top_k_answer = tf.placeholder(dtype=tf.int32)
					self.outputs_prob, self.outputs_index = tf.nn.top_k(prob, self.top_k_answer)

				if self.is_train:
					with tf.name_scope('Loss'):
						# 取正样本
						hit_prob = tf.slice(prob, [0, 0], [-1, 1])
						self.loss = -tf.reduce_sum(tf.log(hit_prob)) / self.batch_size

					with tf.name_scope('Accuracy'):
						output_train = tf.argmax(prob, axis=1)
						self.accuracy = tf.reduce_sum(
							tf.cast(tf.equal(output_train, tf.zeros_like(output_train)), dtype=tf.float32)) / self.batch_size

					# 优化并进行梯度修剪
					with tf.name_scope('Train'):
						optimizer = tf.train.AdamOptimizer(self.learning_rate)
						# 分解成梯度列表和变量列表
						grads, vars = zip(*optimizer.compute_gradients(self.loss))
						# 梯度修剪
						gradients, _ = tf.clip_by_global_norm(grads, 5)  # clip gradients
						# 将每个梯度以及对应变量打包
						self.train_op = optimizer.apply_gradients(zip(gradients, vars))

			# 设置模型存储所需参数
			self.saver = tf.train.Saver()

	def train (self):
		with tf.Session(graph=self.graph) as self.session:
			# 判断模型是否存在
			if os.path.exists(self.model_save_checkpoint):
				# 恢复变量
				self.saver.restore(self.session, self.model_save_name)
			else:
				# 初始化变量
				self.session.run(tf.global_variables_initializer())

			# 开始迭代，使用Adam优化的随机梯度下降法，并将结果输出到日志文件
			print('training------')
			index_list = [i for i in range(self.q_size)]
			for i in range(self.epoch_steps):
				total_loss = 0
				total_accuracy = 0
				for j in range(self.q_size // self.batch_size):
					if self.is_sample:
						sample_list = systematic_sampling(index_list, self.batch_size)
						q_set = []
						t_set = []
						q_actual_length = []
						t_actual_length = []
						for index in sample_list:
							q_set.append(self.q_set[index])
							t_set.append(self.t_set[index])
							q_actual_length.append(self.q_actual_length[index])
							t_actual_length.append(self.t_actual_length[index])
					else:
						q_set = self.q_set[j * self.batch_size:(j + 1) * self.batch_size]
						t_set = self.t_set[j * self.batch_size:(j + 1) * self.batch_size]
						q_actual_length = self.q_actual_length[j * self.batch_size:(j + 1) * self.batch_size]
						t_actual_length = self.t_actual_length[j * self.batch_size:(j + 1) * self.batch_size]
					feed_dict = {self.q_inputs: q_set, self.q_inputs_actual_length: q_actual_length,
					             self.t_inputs: t_set, self.t_inputs_actual_length: t_actual_length}
					_, loss, accuracy = self.session.run([self.train_op, self.loss, self.accuracy], feed_dict=feed_dict)
					total_loss += loss
					total_accuracy += accuracy
				print('[epoch:%d] loss %f accuracy %f' % (i, total_loss / (self.q_size // self.batch_size),
				                                          total_accuracy / (self.q_size // self.batch_size)))

			# 保存模型
			print('save model------')
			self.saver.save(self.session, self.model_save_name)

		pass

	def start_session(self):
		self.session = tf.Session(graph=self.graph)
		self.saver.restore(self.session, self.model_save_name)

	def inference (self, top_k):
		feed_dict = {self.q_inputs: self.q_set, self.q_inputs_actual_length: self.q_actual_length,
		             self.t_final_state: self.t_set, self.t_size: len(self.t_set), self.top_k_answer: top_k,
		             self.batch_size: len(self.q_set)}
		prob, index = self.session.run([self.outputs_prob, self.outputs_index], feed_dict=feed_dict)

		return prob, index

	def extract_t_pre (self):
		with tf.Session(graph=self.graph) as self.session:
			self.saver.restore(self.session, self.model_save_name)

			feed_dict = {self.t_inputs: self.t_set, self.t_inputs_actual_length: self.t_actual_length}
			t_state = self.session.run(self.t_final_state, feed_dict=feed_dict)

			return t_state


class LayerNormalization(tf.layers.Layer):
	def __init__ (self, hidden_size):
		super(LayerNormalization, self).__init__()
		self.hidden_size = hidden_size

	def build (self, _):
		self.scale = tf.get_variable("layer_norm_scale", [self.hidden_size], initializer=tf.ones_initializer())
		self.bias = tf.get_variable("layer_norm_bias", [self.hidden_size], initializer=tf.zeros_initializer())
		self.built = True

	def call (self, x, epsilon=1e-6):
		mean = tf.reduce_mean(x, axis=[-1], keepdims=True)
		variance = tf.reduce_mean(tf.square(x - mean), axis=[-1], keepdims=True)
		norm_x = (x - mean) * tf.rsqrt(variance + epsilon)
		return norm_x * self.scale + self.bias
