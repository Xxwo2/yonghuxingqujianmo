#encoding: utf-8
import os
import numpy as np
import pandas as pd
import pickle,gc
import tensorflow as tf
from scipy.stats import spearmanr
from sklearn.utils import shuffle
from keras.callbacks import Callback
from sklearn.metrics import roc_auc_score,log_loss
from sklearn.preprocessing import LabelEncoder
from keras.preprocessing.sequence import pad_sequences
from keras.layers import Dense, Input, Embedding, Dropout, Activation
from keras.layers.merge import concatenate
from keras.models import Model
from keras.layers.normalization import BatchNormalization
from keras.layers import GlobalMaxPooling1D, GlobalAveragePooling1D, Flatten

os.environ["CUDA_VISIBLE_DEVICES"] = "3"
bst_model_path = 'best.mdl'
batch_size = 2048
path = "/home/yingda1/202/"

#读入用户交互数据
train_columns = ['user_id', 'photo_id', 'click', 'like', 'follow', 'time', 'playing_time', 'duration_time']
train_interaction = pd.read_table(path+'./train/train_interaction.txt', header=None)
train_interaction.columns = train_columns

test_columns = ['user_id', 'photo_id', 'time', 'duration_time']
test_interaction = pd.read_table(path+'./test/test_interaction.txt', header=None)
test_interaction.columns = test_columns

data = pd.concat([train_interaction, test_interaction])

le_user = LabelEncoder()
data['user_id'] = le_user.fit_transform(data['user_id'])
le_photo = LabelEncoder()
data['photo_id'] = le_photo.fit_transform(data['photo_id'])

#读入user_doc
user_doc = pd.read_csv(path+'./data/user_doc.csv',header=0)
print("read doc over")

#读入user.emb,photo.emb，构建用户和视频的emb初始化矩阵
def read_emb(path):
    count = 0
    f = open(path, 'r')
    emb_dict = dict()
    for line in f:
        if count == 0:
            count += 1
            continue
        line = line.split(' ')
        id = int(line[0])

        weights = line[1:]
        weights = np.array([float(i) for i in weights])
        count += 1
        emb_dict[id] = weights
    return emb_dict

user_emb = read_emb(path+'./data/user.emb')
photo_emb = read_emb(path+'./data/photo.emb')

EMBEDDING_DIM_USER = 64
nb_users = data['user_id'].nunique()
embedding_matrix_user = np.zeros((nb_users, EMBEDDING_DIM_USER))
print(embedding_matrix_user.shape)
for word in user_emb.keys():
    embedding_vector = user_emb.get(word)
    embedding_matrix_user[word] = embedding_vector
embedding_matrix_user = embedding_matrix_user.astype(np.float32)

EMBEDDING_DIM_PHOTO = 64
nb_photos = data['photo_id'].nunique()
embedding_matrix_photo = np.zeros((nb_photos, EMBEDDING_DIM_PHOTO))
print(embedding_matrix_photo.shape)
for word in photo_emb.keys():
    embedding_vector = photo_emb.get(word)
    embedding_matrix_photo[word] = embedding_vector
embedding_matrix_photo = embedding_matrix_photo.astype(np.float32)

#搭建网络
embedding_layer_user = Embedding(nb_users,
                                 EMBEDDING_DIM_USER,
                                 weights=[embedding_matrix_user],
                                 trainable=False)
embedding_layer_user2 = Embedding(nb_users,
                                  EMBEDDING_DIM_USER,
                                  trainable=True)
embedding_layer_photo = Embedding(nb_photos,
                                  EMBEDDING_DIM_PHOTO,
                                  weights=[embedding_matrix_photo],
                                  trainable=False)
MAX_SENTENCE_LENGTH = 30
input_user = Input(shape=(1,), dtype='int32')
input_photo = Input(shape=(1,), dtype='int32')
input_user_mean = Input(shape=(MAX_SENTENCE_LENGTH,), dtype='int32')

embedded_user = embedding_layer_user(input_user)
embedded_user2 = embedding_layer_user2(input_user)
embedded_photo = embedding_layer_photo(input_photo)

embedded_user2_agg = embedding_layer_user2(input_user_mean)

embedded_user = Flatten()(embedded_user)
embedded_user2 = Flatten()(embedded_user2)
embedded_photo = Flatten()(embedded_photo)
embedded_user2_max = GlobalMaxPooling1D()(embedded_user2_agg)

flatten_list = [
    embedded_user,
    embedded_user2,
    embedded_photo,
    embedded_user2_max,
]

act = 'relu'
merged = concatenate(flatten_list, name='match_concat')
merged = Dense(128, activation=act)(merged)
merged = BatchNormalization()(merged)
merged = Dropout(0.25)(merged)
preds = Dense(1, activation='sigmoid')(merged)

model = Model(inputs=[input_user,input_photo,input_user_mean],outputs=preds)
model.compile(loss='binary_crossentropy',
              optimizer='adam',
              metrics=['accuracy'])
print(model.summary())

#准备输入数据
len_train = train_interaction.shape[0]
train = data[:len_train]
test = data[len_train:]
del data

user_doc['photo_id'] = user_doc['photo_id'].astype(int)
user_doc['user_id_doc'] = user_doc['user_id_doc'].apply(lambda x:[int(s) for s in x.split(' ')])
train = pd.merge(train,user_doc,on='photo_id',how='left')
test = pd.merge(test,user_doc,on='photo_id',how='left')

#划分训练集和验证集
train = train.sort_values('time')
train_tr = train.iloc[:int(train.shape[0]*0.8),:].copy()
train_te = train.iloc[int(train.shape[0]*0.8):,:].copy()
te_photo_ids = list(set(train_te['photo_id'].values)-set(train_tr['photo_id'].values))
train_te = train_te.loc[train_te.photo_id.isin(te_photo_ids)]
train_tr,train_te = shuffle(train_tr),shuffle(train_te)

train_tr_user_mean = pad_sequences(train_tr['user_id_doc'].values, maxlen=MAX_SENTENCE_LENGTH)
train_te_user_mean = pad_sequences(train_te['user_id_doc'].values, maxlen=MAX_SENTENCE_LENGTH)
test_user_mean = pad_sequences(test['user_id_doc'].values, maxlen=MAX_SENTENCE_LENGTH)

X_train = [
    train_tr['user_id'].values, #userid
    train_tr['photo_id'].values, #photoid
    train_tr_user_mean, #photo对应的所有用户
]

X_test = [
    train_te['user_id'].values,
    train_te['photo_id'].values,
    train_te_user_mean,
]

X_t = [
    test['user_id'].values,
    test['photo_id'].values,
    test_user_mean,
]

y_train = train_tr['click'].values
y_test = train_te['click'].values

# AucCallback
class AucCallback(Callback):

    def __init__(self, validation_data=(), patience=25, is_regression=False, best_model_name='best_keras.mdl',
                 feval='roc_auc_score', batch_size=128):
        super(Callback, self).__init__()

        self.patience = patience
        self.X_test, self.y_test = validation_data  # tuple of validation X and y
        self.best = -np.inf
        self.wait = 0  # counter for patience
        self.best_model = None
        self.best_model_name = best_model_name
        self.is_regression = is_regression
        self.y_test = self.y_test  # .astype(np.int)
        self.feval = feval
        self.batch_size = batch_size

    def on_epoch_end(self, epoch, logs={}):
        p = []
        p = model.predict(self.X_test,batch_size = batch_size)

        current = 0.0
        if self.feval == 'roc_auc_score':
            current += roc_auc_score(self.y_test.ravel(), p.ravel())

        if current > self.best:
            self.best = current
            self.wait = 0
            self.model.save_weights(self.best_model_name, overwrite=True)
        else:
            if self.wait >= self.patience:
                self.model.stop_training = True
                print('Epoch %05d: early stopping' % (epoch))
            self.wait += 1  # incremental the number of times without improvement
        print('Epoch %d Auc: %f | Best Auc: %f \n' % (epoch, current, self.best))

auc_callback = AucCallback(validation_data=(X_test, y_test), patience=0, best_model_name=bst_model_path,
                           batch_size=batch_size)
callbacks = [auc_callback]

#训练模型
hist = model.fit(X_train, y_train, validation_data=(X_test, y_test), epochs=10, batch_size=batch_size, shuffle=True,
                 callbacks=callbacks)

#预测
model.load_weights(bst_model_path)
y_sub = model.predict(X_t,batch_size=batch_size)
submission = pd.DataFrame()
submission['user_id'] = test_interaction['user_id']
submission['photo_id'] = test_interaction['photo_id']
submission['click_probability'] = y_sub
submission['click_probability'].apply(lambda x:float('%.6f' % x))
submission.to_csv('./data/submission1.txt',sep='\t',index=False,header=False)