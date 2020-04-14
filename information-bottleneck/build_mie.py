import torch
import torch.optim as optim
import torch.nn as nn
from torchvision.datasets import MNIST
from torchvision.transforms import ToTensor
from torch.utils.data import DataLoader

import argparse
import numpy as np
import pandas as pd
from random import randint, seed
import os
import matplotlib.pyplot as plt
import time

from data_utils import *
from evaluation import *
from helper import *
from models import VAE, MLP, MIEstimator


def train_encoder(dnn_hidden_units, dnn_input_units=784, dnn_output_units=10, enc_type='MLP', weight_decay=0, num_epochs=10, eval_freq=1, dropout=False, p_dropout=0.5):
    print('Weight decay to be applied: ', weight_decay)
    if dropout:
        print('train_encoder p_dropout %s' % p_dropout)
    if enc_type == 'MLP':
        Net = MLP(dnn_input_units, dnn_hidden_units, dnn_output_units, FLAGS, neg_slope=FLAGS.neg_slope, dropout=dropout, p_dropout=p_dropout).to(device)
    elif enc_type =='VAE':
        Net = VAE(dnn_input_units, dnn_output_units, dnn_hidden_units[-1]).to(device)
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(Net.parameters(), lr=learning_rate, weight_decay=weight_decay) #default 1e-3

    start_time = time.time()
    max_accuracy = 0

    for epoch in range(num_epochs):
        for X_train, y_train in train_loader:
            X_train, y_train = X_train.flatten(start_dim=1).to(device), y_train.to(device)
            optimizer.zero_grad()
            if enc_type =='VAE':
                (mu, std), out, z_train = Net(X_train)
                loss = criterion(out, y_train)
            else:
                out = Net(X_train)
                loss = criterion(out, y_train)
            loss.backward()
            optimizer.step()
        if epoch % eval_freq == 0 or epoch == num_epochs - 1:

                print('\n'+'#'*30)
                print('Training epoch - %d/%d' % (epoch+1, num_epochs))

                if enc_type == 'VAE':
                    (mu, std), out_test, z_test = Net(X_test)
                    test_loss = criterion(out_test, y_test)
                else:
                    out_test = Net(X_test)
                    test_loss = criterion(out_test, y_test)
                test_accuracy = accuracy(out_test, y_test)
                if test_accuracy > max_accuracy:
                    max_accuracy = test_accuracy

                print('Train: Accuracy - %0.3f, Loss - %0.3f' % (accuracy(out, y_train), loss))
                print('Test: Accuracy - %0.3f, Loss - %0.3f' % (test_accuracy, test_loss))
                print('Elapsed time: ', time.time() - start_time)
                print('#'*30,'\n')
                if test_accuracy == 1 and test_loss == 0:
                    break
    Net.best_performance = max_accuracy
    return Net

def train_encoder_VIB(dnn_hidden_units, dnn_input_units=784, dnn_output_units=10, enc_type='MLP', num_epochs=10, eval_freq=1):
    
    Net = VAE(dnn_input_units, dnn_output_units, dnn_hidden_units[-1]).to(device)
    
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(Net.parameters(),lr=1e-4,betas=(0.5,0.999))
    scheduler = optim.lr_scheduler.ExponentialLR(optimizer,gamma=0.97)
    
    start_time = time.time()
    max_accuracy = 0
    beta = FLAGS.vib_beta
    for epoch in range(num_epochs):
        for X_train, y_train in train_loader:
            # beta = scheduler(epoch)
            
            X_train, y_train = X_train.flatten(start_dim=1).to(device), y_train.to(device)
            optimizer.zero_grad()
            (mu, std), out, z_train = Net(X_train)
            
            class_loss = criterion(out, y_train).div(math.log(2)) #make log of base 2
            info_loss = -0.5 * (1 + 2 * std.log() - mu.pow(2) - std.pow(2)).sum(1).mean().div(math.log(2))
            total_loss = class_loss + beta * info_loss

            izy_bound = math.log(10,2) - class_loss
            izx_bound = info_loss

            total_loss.backward()
            optimizer.step()
        if epoch % eval_freq == 0 or epoch == num_epochs - 1:

                print('\n'+'#'*30)
                print('Training epoch - %d/%d' % (epoch+1, num_epochs))

                (mu, std), out_test, z_test = Net(X_test)
                test_class_loss = criterion(out, y_train).div(math.log(2)) #make log of base 2
                test_info_loss = -0.5 * (1 + 2 * std.log() - mu.pow(2) - std.pow(2)).sum(1).mean().div(math.log(2))
                test_total_loss = test_class_loss + beta * test_info_loss
                test_accuracy = accuracy(out_test, y_test)
                if test_accuracy > max_accuracy:
                    max_accuracy = test_accuracy

                print('Train: Accuracy - %0.3f, Loss - %0.3f' % (accuracy(out, y_train), total_loss))
                print('Test: Accuracy - %0.3f, Loss - %0.3f' % (test_accuracy, test_total_loss))
                print('Upperbound I(X, T)', izx_bound.item())
                print('Lowerbound I(T, Y)', izy_bound.item())
                print('Elapsed time: ', time.time() - start_time)
                print('#'*30,'\n')
                if test_accuracy == 1 and test_total_loss == 0:
                    break
    Net.best_performance = max_accuracy
    return Net

def train_MI(encoder, beta=1, mie_on_test=False, seed=69, num_epochs=2000, eval_freq=1, layer=''):
    
    if not mie_on_test:
        loader = train_loader
    else:
        loader = test_loader

    if enc_type == 'VAE':
       (_, _), _, z_test = encoder(X_test)
    else:
       z_test = encoder(X_test)
    z_test = torch.tensor(z_test, requires_grad=False).to(device)

    x_dim, y_dim, z_dim = X_test.shape[-1], dnn_output_units, z_test.shape[-1]

    mi_estimator_X = MIEstimator(x_dim, z_dim).to(device)
    mi_estimator_Y = MIEstimator(z_dim, y_dim).to(device)

    optimizer = optim.Adam([
    {'params': mi_estimator_X.parameters(), 'lr':mie_lr_x}, #default 1e-5
    {'params': mi_estimator_Y.parameters(), 'lr':mie_lr_y}, #default 1e-4
    ])
    if beta == 0:
        use_scheduler = True
        beta_scheduler = ExponentialScheduler(start_value=1e-6, end_value=1, n_iterations=500, start_iteration=20)
    else:
        use_scheduler = False

    start_time = time.time()
    max_MI_x = max_MI_y = 0
    train_x = train_y = True
    mi_mean_est_all = {'X': [], 'Y': []}
    
    for epoch in range(num_epochs):
        if use_scheduler:
            beta = beta_scheduler(epoch)
        mi_over_epoch = {'X': [], 'Y': []}
        for X, y in loader:
            y = onehot_encoding(y)
            X, y = X.flatten(start_dim=1).to(device), y.float().to(device)

            if enc_type == 'VAE':
                (_, _), _, z = encoder(X)
            else:
                z = encoder(X)

            optimizer.zero_grad()

            mi_gradient_X, mi_estimation_X = mi_estimator_X(X, z)
            mi_gradient_X = mi_gradient_X.mean()
            mi_estimation_X = mi_estimation_X.mean()

            mi_gradient_Y, mi_estimation_Y = mi_estimator_Y(z, y)
            mi_gradient_Y = mi_gradient_Y.mean()
            mi_estimation_Y = mi_estimation_Y.mean()
                    
            loss_mi = - mi_gradient_Y - beta * mi_gradient_X
            loss_mi.backward()
            optimizer.step()

            mi_over_epoch['X'].append(mi_estimation_X.item())
            mi_over_epoch['Y'].append(mi_estimation_Y.item())

        mi_over_epoch['X'] = np.array(mi_over_epoch['X'])
        mi_over_epoch['Y'] = np.array(mi_over_epoch['Y'])

        # Discard top and bottom 5% to avoid numerical outliers
        tmp = mi_over_epoch['X'][mi_over_epoch['X'] < np.quantile(mi_over_epoch['X'], 1 - mie_k_discard/100)]
        tmp = tmp[tmp > np.quantile(mi_over_epoch['X'], mie_k_discard/100)]
        mi_over_epoch['X'] = tmp

        tmp = mi_over_epoch['Y'][mi_over_epoch['Y'] < np.quantile(mi_over_epoch['Y'], 1 - mie_k_discard/100)]
        tmp = tmp[tmp > np.quantile(mi_over_epoch['Y'], mie_k_discard/100)]
        mi_over_epoch['Y'] = tmp

        if np.mean(mi_over_epoch['X']) > max_MI_x:
            max_MI_x = np.mean(mi_over_epoch['X'])
        if np.mean(mi_over_epoch['Y']) > max_MI_y:
            max_MI_y = np.mean(mi_over_epoch['Y'])
        mi_mean_est_all['X'].append(np.mean(mi_over_epoch['X']))
        mi_mean_est_all['Y'].append(np.mean(mi_over_epoch['Y']))
            
        if epoch % eval_freq == 0 or epoch == num_epochs - 1:

            print('#'*30)
            print('Step - ', epoch)
            print('Beta - ', beta)
                
            if epoch >= 2:
                delta_x = mi_mean_est_all['X'][-2] - mi_mean_est_all['X'][-1]
                print('Delta X: ', delta_x)
                delta_y = mi_mean_est_all['Y'][-2] - mi_mean_est_all['Y'][-1]
                print('Delta Y: ', delta_y)
            if epoch >= 10:
                print('\nMean MI X for last 10', np.mean(mi_mean_est_all['X'][-10:]))
                print('Mean MI Y for last 10', np.mean(mi_mean_est_all['Y'][-10:]))
            if epoch >= 20:
                print('\nMean MI X for last 20', np.mean(mi_mean_est_all['X'][-20:]))
                print('Mean MI Y for last 20', np.mean(mi_mean_est_all['Y'][-20:]))
            if epoch >= 30:
                print('\nMean MI X for last 30', np.mean(mi_mean_est_all['X'][-30:]))
                print('Mean MI Y for last 30', np.mean(mi_mean_est_all['Y'][-30:]))
            if epoch >= w_size:
                print('\nMean MI X for last %s - %s' % (w_size, np.mean(mi_mean_est_all['X'][-w_size:])))
                print('Mean MI Y for last %s - %s' % (w_size, np.mean(mi_mean_est_all['Y'][-w_size:])))
            if epoch >= 2*w_size:
                print('Latest window mean value: ', np.mean(mi_mean_est_all['X'][-w_size:]))
                print('Previous window mean value', np.mean(mi_mean_est_all['X'][-2*w_size:-w_size]))
            mi_df = pd.DataFrame.from_dict(mi_mean_est_all)
            if not os.path.exists(FLAGS.result_path+'/mie_values'):
                os.makedirs(FLAGS.result_path+'/mie_values')
            mi_df.to_csv(FLAGS.result_path+'/mie_values/mie_%s_%s__l%s_w%s_s%s.csv' % (enc_type.lower(), 'test' if mie_on_test else 'train', layer, int(1/weight_decay) if weight_decay != 0 else 0, seed), sep=' ')
            print('Max I_est(X, Z) - %s' % max_MI_x)
            print('Max I_est(Z, Y) - %s' % max_MI_y)
            print('Elapsed time training MI for %s: %s' % (layer, time.time() - start_time))
            print('#'*30,'\n')

            plot_mie_curve(FLAGS, mi_df, layer, seed)
        

        # if epoch >= w_size and np.mean(mi_mean_est_all['X'][-w_size:]) > max_MI_x - 1e-1 or epoch == num_epochs - 1:
        if epoch >= w_size and np.mean(mi_mean_est_all['X'][-2*w_size:-w_size]) > np.mean(mi_mean_est_all['X'][-w_size:]) - mie_converg_bound or epoch == num_epochs - 1:
            train_x = False

        if epoch >= w_size and np.mean(mi_mean_est_all['Y'][-2*w_size:-w_size]) > np.mean(mi_mean_est_all['Y'][-w_size:]) - mie_converg_bound or epoch == num_epochs - 1:
            train_y = False
        if not mie_train_till_end:
            if train_x == False and train_y == False:
                print('Convergence criteria successfully satisified')
                break

    plot_mie_curve(FLAGS, mi_df, layer, seed)            
    return max_MI_x, max_MI_y, mi_estimator_X, mi_estimator_Y

def main():
    print('main p_dropout %s' % p_dropout)
    if FLAGS.use_of_vib:
        Encoder = train_encoder_VIB(dnn_hidden_units, enc_type=enc_type, num_epochs=num_epochs)
    else:
        Encoder = train_encoder(dnn_hidden_units, enc_type=enc_type, num_epochs=num_epochs, weight_decay=weight_decay, dropout=dropout, p_dropout=p_dropout)
    print('Best achieved performance: ', Encoder.best_performance)
    print(Encoder)

    if FLAGS.layers_to_track:
        layers_to_track = FLAGS.layers_to_track.split(",")
        layers_to_track = [int(layer_num) for layer_num in layers_to_track]
    else:
        layers_to_track = [1]

    pos_layers = - (np.array(layers_to_track) + 1)
    layers_names = []
    for pos in pos_layers:
        layers_names.append(list(get_named_layers(Encoder).keys())[pos].split('_')[0])


    if FLAGS.seeds:
        seeds = FLAGS.seeds.split(",")
        seeds = [int(seed) for seed in seeds]
    else:
        seeds = [default_seed]

    mie_layers = {layer:{s:(np.nan, np.nan) for s in seeds} for layer in layers_names}
    start_time = time.time()
        

    for i in range(len(seeds)):

        print('\nRunning for seed %d out of %d' % (i+1, len(seeds)))
        torch.manual_seed(seeds[i])
        np.random.seed(seeds[i])
        seed(seeds[i])
        
        
        for j in range(len(layers_names)):
            layer = layers_names[j]
            if enc_type == 'MLP':
                MI_X, MI_Y, MIE_X, MIE_Y = train_MI(Encoder.models[layer], beta=mie_beta, mie_on_test=mie_on_test, seed=seeds[i], layer=layer, num_epochs=mie_num_epochs)
            else:
                MI_X, MI_Y, MIE_X, MIE_Y = train_MI(Encoder, beta=mie_beta, mie_on_test=mie_on_test, seed=seeds[i], num_epochs=mie_num_epochs)

            if mie_save_models:
                if not os.path.exists(FLAGS.result_path+'/estimator_models'):
                    os.makedirs(FLAGS.result_path+'/estimator_models')
                torch.save(MIE_X.state_dict(), FLAGS.result_path + '/estimator_models/mie_x_%s_%s_l%s_b%s_w%s_s%s.pt' % (enc_type.lower(), 'test' if mie_on_test else 'train', layer, mie_beta, int(1/weight_decay) if weight_decay != 0 else 0, seeds[i]))
                torch.save(MIE_Y.state_dict(), FLAGS.result_path + '/estimator_models/mie_y_%s_%s_l%s_b%s_w%s_s%s.pt' % (enc_type.lower(), 'test' if mie_on_test else 'train', layer, mie_beta, int(1/weight_decay) if weight_decay != 0 else 0, seeds[i]))
            
            mie_layers[layer][seeds[i]] = (MI_X, MI_Y)
            print('MI values for %s - %s, %s' % (layer, MI_X, MI_Y))
            print(mie_layers)
            build_information_plane(mie_layers, layers_names[:j+1], seeds[:i+1], FLAGS)
        
    print('Elapsed time - ', time.time() - start_time)


if __name__=='__main__':
    # Command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument('--enc_type', type = str, default = 'MLP',
                        help='Type of encoder to train')
    parser.add_argument('--dropout', type = bool, default = False,
                        help='Apply dropout after each layer of encoder')
    parser.add_argument('--p_dropout', type = float, default = 0.5,
                        help='Probability of dropout')
    parser.add_argument('--input_dropout', type = bool, default = False,
                        help='Apply dropout to the input layer of encoder')
    parser.add_argument('--p_input_dropout', type = float, default = 0.8,
                        help='Probability of dropout')
    parser.add_argument('--dnn_hidden_units', type = str, default = '1024,512,256,128,64',
                        help='Comma separated list of number of units in each hidden layer')
    parser.add_argument('--default_seed', type = int, default = 69,
                        help='Default seed for encoder training')
    parser.add_argument('--seeds', type = str, default = '69',
                        help='Comma separated list of random seeds')
    parser.add_argument('--layers_to_track', type = str, default = '1',
                        help='Comma separated list of inverse positions of encoding layers to evaluate (starting from 1)')
    parser.add_argument('--learning_rate', type = float, default = 1e-3,
                        help='Learning rate for encoder training')
    parser.add_argument('--mie_lr_x', type = float, default = 1e-5,
                        help='Learning rate for estimation of mutual information with input')
    parser.add_argument('--mie_lr_y', type = float, default = 1e-4,
                        help='Learning rate for estimation of mutual information with target')
    parser.add_argument('--mie_beta', type = float, default = 1,
                        help='Lagrangian multiplier representing prioirity of MI(z, y) over MI(x, z)')
    parser.add_argument('--vib_beta', type = float, default = 1e-3,
                        help='Lagrangian multiplier representing prioirity of MI(z, y) over MI(x, z)')
    parser.add_argument('--use_of_vib', type = bool, default = False,
                        help='Need to train using Variational Information Bottleneck objective')
    
    parser.add_argument('--mie_on_test', type = bool, default = False,
                        help='Whether to build MI estimator using training or test set')
    parser.add_argument('--mie_k_discard', type = float, default = 5,
                        help='Per cent of top and bottom MI estimations to discard')
    parser.add_argument('--mie_converg_bound', type = float, default = 5e-2,
                        help='Tightness of bound for the convergence criteria')
    parser.add_argument('--weight_decay', type = float, default = 0,
                      help='Value of weight decay applied to optimizer')
    parser.add_argument('--num_epochs', type = int, default = 10,
                        help='Number of epochs to do training')
    parser.add_argument('--mie_num_epochs', type = int, default = 100,
                        help='Max number of epochs to do MIE training')
    parser.add_argument('--mie_save_models', type = bool, default = False,
                      help='Need to store MIE models learnt')
    parser.add_argument('--mie_train_till_end', type = bool, default = False,
                      help='Need to train for mie_num_epochs or convergence')
    parser.add_argument('--num_classes', type = int, default = 10,
                        help='Number of classes')
    parser.add_argument('--batch_size', type = int, default = 64,
                        help='Batch size to run trainer.')
    parser.add_argument('--eval_freq', type=int, default=1,
                            help='Frequency of evaluation on the test set')
    parser.add_argument('--w_size', type=int, default=20,
                            help='Window size to count towards convergence criteria')
    parser.add_argument('--neg_slope', type=float, default=0.02,
                        help='Negative slope parameter for LeakyReLU')
    parser.add_argument('--result_path', type = str, default = 'results_mie',
                      help='Directory for storing results')
                    
    
    parser.add_argument('--comment', type = str, default = '',
                      help='Additional comments on the runtime set up')

    FLAGS, unparsed = parser.parse_known_args()

    print_flags(FLAGS)

    global_start_time = time.time()

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    cuda = torch.cuda.is_available()

    default_seed = FLAGS.default_seed 
    num_classes = FLAGS.num_classes
    batch_size = FLAGS.batch_size 
    enc_type = FLAGS.enc_type
    dropout = FLAGS.dropout
    p_dropout = FLAGS.p_dropout
    weight_decay = FLAGS.weight_decay 
    num_epochs = FLAGS.num_epochs
    learning_rate = FLAGS.learning_rate
    mie_lr_x = FLAGS.mie_lr_x
    mie_lr_y = FLAGS.mie_lr_y
    mie_num_epochs = FLAGS.mie_num_epochs
    mie_beta = FLAGS.mie_beta
    mie_on_test = FLAGS.mie_on_test
    mie_k_discard = FLAGS.mie_k_discard
    mie_train_till_end = FLAGS.mie_train_till_end
    mie_converg_bound = FLAGS.mie_converg_bound
    mie_save_models = FLAGS.mie_save_models
    w_size = FLAGS.w_size

    if FLAGS.use_of_vib:
        enc_type = FLAGS.enc_type = 'VAE'

    if FLAGS.comment != '':
        FLAGS.result_path += '/FLAGS.comment'

    np.random.seed(default_seed)
    torch.manual_seed(default_seed)
    seed(default_seed)

    # Loading the MNIST dataset
    train_set = MNIST('./data/MNIST', download=True, train=True, transform=ToTensor())
    test_set = MNIST('./data/MNIST', download=True, train=False, transform=ToTensor())

    # Initialization of the data loader
    train_loader = DataLoader(train_set, batch_size=batch_size, shuffle=True, num_workers=1)
    test_loader = DataLoader(test_set, batch_size=batch_size, shuffle=True, num_workers=1)

    # Single time define the testing set. Keep it fixed until the end
    X_test, y_test = build_test_set(test_loader, device)

    if FLAGS.dnn_hidden_units:
        dnn_hidden_units = FLAGS.dnn_hidden_units.split(",")
        dnn_hidden_units = [int(dnn_hidden_unit_) for dnn_hidden_unit_ in dnn_hidden_units]
    else:
        dnn_hidden_units = []
    
    dnn_input_units = X_test.shape[-1]
    dnn_output_units = num_classes

    main()

    print('Excecution finished with overall time elapsed - %s' % (time.time() - global_start_time))
    print_flags(FLAGS)
