import dataloader
import numpy as np
import os
import utils
import keras
from argparse import ArgumentParser
import config
import tqdm
import matplotlib.pyplot as plt
from sklearn.metrics import label_ranking_average_precision_score

def opts_parser():
    descr = "Trains a neural network."
    parser = ArgumentParser(description=descr)
    parser.add_argument('modelfile', metavar='MODELFILE',
            type=str,
            help='File to save the learned weights to')
    config.prepare_argument_parser(parser)
    return parser

def save_learning_curve(metric, val_metric, filename, title, ylabel):
    plt.plot(metric)
    plt.plot(val_metric)
    plt.title(title)
    plt.ylabel(ylabel)
    plt.xlabel('Epoch')
    plt.legend(['train', 'validation'], loc='upper left')
    plt.grid()
    plt.ylim([0, 1.05])
    plt.savefig('plots/' + filename)
    plt.close()

def lwlrap_metric(y_true, y_pred):
    sample_weight = np.sum(y_true > 0, axis=1)
    nonzero_weight_sample_indices = np.flatnonzero(sample_weight > 0)
    overall_lwlrap = label_ranking_average_precision_score(
        y_true[nonzero_weight_sample_indices, :] > 0,
        y_pred[nonzero_weight_sample_indices, :],
        sample_weight=sample_weight[nonzero_weight_sample_indices])
    return overall_lwlrap

def save_model(modelfile, network, cfg):
    """
    Saves the learned weights to `filename`, and the corresponding
    configuration to ``os.path.splitext(filename)[0] + '.vars'``.
    """
    config.write_config_file(modelfile + '_auto.vars', cfg)
    network_yaml = network.to_yaml()
    with open(modelfile+".yaml", 'w') as yaml_file:
        yaml_file.write(network_yaml)

def main():
    label_mapping, inv_label_mapping = dataloader.get_label_mapping()
    parser = opts_parser()
    options = parser.parse_args()
    modelfile = options.modelfile
    cfg = config.from_parsed_arguments(options)

    # keras configurations
    keras.backend.set_image_data_format('channels_last')

    # classification threshold delta
    clf_delta = 0.05

    verified_files_dict = dataloader.get_verified_files_dict()
    noisy_files_dict = dataloader.get_unverified_files_dict()
    total_files_dict = dict(verified_files_dict, **noisy_files_dict)
    fold = 1

    print('Loading data...')
    #for fold in range(1,5):
    train_files = []
    eval_files = []
    with open('../datasets/cv/fold{}_curated_train'.format(fold), 'r') as in_file:
        train_files.extend(in_file.readlines())
    with open('../datasets/cv/fold{}_noisy_train'.format(fold), 'r') as in_file:
        train_files.extend(in_file.readlines())

    with open('../datasets/cv/fold{}_curated_eval'.format(fold), 'r') as in_file:
        eval_files.extend(in_file.readlines())

    print('Loading model')
    # import model from file
    selected_model = utils.import_model(modelfile)

    # instantiate neural network
    print("Preparing training function...")

    train_formats = (cfg['feature_height'], cfg['feature_width'], cfg['channels'])
    network = selected_model.architecture(train_formats, cfg['num_classes'])

    # Add optimizer and compile model
    print("Compiling model ...")
    optimizer = keras.optimizers.Adam(lr=cfg['lr'])
    network.compile(optimizer=optimizer, loss=cfg["loss"], metrics=['acc'])

    print("Preserving architecture and configuration ..")
    save_model(os.path.join('models', modelfile.replace('.py', '')) + '_fold{}'.format(fold), network, cfg)

    # Add batch creator, and training procedure
    val_loss = []
    val_acc = []
    train_loss = []
    train_acc = []
    epochs_without_decrase = 0
    lwlraps_eval = []
    lwlraps_train = []

    # run training loop
    print("Training:")
    for epoch in range(cfg['epochs']):

        epoch_train_loss = []
        epoch_train_acc = []
        batch_val_loss = []
        batch_val_acc = []
        epoch_lwlrap_train = []
        epoch_lwlrap_eval = []

        train_batches = dataloader.load_batches(train_files, cfg['batchsize'], infinite=True, shuffle=True)
        train_eval_batches = dataloader.load_batches(train_files, cfg['batchsize'], infinite=False, shuffle=False)
        eval_batches = dataloader.load_batches(eval_files, cfg['batchsize'], infinite=False)

        for _ in tqdm.trange(
                cfg['epochsize'],
                desc='Epoch %d/%d:' % (epoch + 1, cfg['epochs'])):

            batch = next(train_batches)
            X_train, y_train = dataloader.load_features(batch, features='mel', num_classes=cfg['num_classes'])
            X_train = X_train[:,:,:,np.newaxis]

            metrics = network.train_on_batch(x=X_train, y=y_train)
            epoch_train_acc.append(metrics[1])
            epoch_train_loss.append(metrics[0])

        print('Loss on training set after epoch {}: {}'.format(epoch, np.mean(epoch_train_loss)))
        print('Accuracy on training set after epoch {}: {}\n'.format(epoch, np.mean(epoch_train_acc)))

        print('Predicting...')
        for batch in tqdm.tqdm(train_eval_batches, desc='Batch'):

            X_train, y_train = dataloader.load_features(batch, features='mel', num_classes=cfg['num_classes'])
            X_train = X_train[:, :, :, np.newaxis]

            preds = network.predict(x=X_train, batch_size=cfg['batchsize'], verbose=0)

            epoch_lwlrap_train.append(lwlrap_metric(np.asarray(y_train), np.asarray(preds)))

        print('Label weighted label ranking average precision on training set after epoch {}: {}'.format(epoch,
                                                                                                         np.mean(epoch_lwlrap_train)))
        train_loss.append(np.mean(epoch_train_loss))
        train_acc.append(np.mean(epoch_train_acc))
        lwlraps_train.append(np.mean(epoch_lwlrap_train))

        for batch_valid in tqdm.tqdm(eval_batches, desc='Batch'):
            X_test, y_test = dataloader.load_features(batch_valid, features='mel', num_classes=cfg['num_classes'])
            X_test = X_test[:, :, :, np.newaxis]

            metrics = network.test_on_batch(x=X_test, y=y_test)

            batch_val_loss.append(metrics[0])
            batch_val_acc.append(metrics[1])

            predictions = network.predict(x=X_test, batch_size=cfg['batchsize'], verbose=0)

            epoch_lwlrap_eval.append(lwlrap_metric(np.asarray(y_test), np.asarray(predictions)))

        lwlraps_eval.append(np.mean(epoch_lwlrap_eval))
        val_acc.append(np.mean(batch_val_acc))
        val_loss.append(np.mean(batch_val_loss))

        print('Loss on validation set after epoch {}: {}'.format(epoch, np.mean(batch_val_loss)))
        print('Accuracy on validation set after epoch {}: {}'.format(epoch, np.mean(batch_val_acc)))
        print('Label weighted label ranking average precision on validation set after epoch {}: {}'.format(epoch,
                                                                                               np.mean(epoch_lwlrap_eval)))

        current_loss = np.mean(batch_val_loss)
        current_acc = np.mean(batch_val_acc)
        current_lwlrap = np.mean(epoch_lwlrap_eval)

        if epoch > 0:
            if current_lwlrap > np.amax(lwlraps_eval):
                epochs_without_decrase = 0
                print("Average lwlrap increased - Saving weights...\n")
                network.save_weights("models/{}_fold{}.hd5".format(modelfile.replace('.py', ''), fold))
            elif not cfg['linear_decay']:
                epochs_without_decrase += 1
                if epochs_without_decrase == cfg['epochs_without_decrease']:
                    lr = keras.backend.get_value(network.optimizer.lr)
                    lr = lr * cfg['lr_decrease']
                    keras.backend.set_value(network.optimizer.lr, lr)
                    print("lwlrap did not increase for the last {} epochs - halfing learning rate...".format(
                        cfg['epochs_without_decrease']))
                    epochs_without_decrase = 0

            if cfg['linear_decay']:
                if epoch >= cfg['start_linear_decay']:
                    lr = keras.backend.get_value(network.optimizer.lr)
                    lr = lr - cfg['lr_decrease']
                    keras.backend.set_value(network.optimizer.lr, lr)
                    print("Decreasing learning rate by {}...".format(cfg['lr_decrease']))
        else:
            print("Average lwlrap increased - Saving weights...\n")
            network.save_weights("models/baseline_fold{}.hd5".format(fold))

        # Save loss and learning curve of trained model
        save_learning_curve(train_acc, val_acc, "{}_fold{}_accuracy_learning_curve.pdf".format(modelfile.replace('.py', ''), fold), 'Accuracy', 'Accuracy')
        save_learning_curve(train_loss, val_loss, "{}_fold{}_loss_curve.pdf".format(modelfile.replace('.py', ''), fold), 'Loss Curve', 'Loss')
        save_learning_curve(lwlraps_train, lwlraps_eval, '{}_fold{}_lwlrap_curve.pdf'.format(modelfile.replace('.py', ''), fold),
                            "Label Weighted Label Ranking Average Precision", 'lwlrap')


if __name__ == '__main__':
    main()
