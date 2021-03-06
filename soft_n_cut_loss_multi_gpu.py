# http://blog.s-schoener.com/2017-12-15-parallel-tensorflow-intro/ for multi gpu training
import cv2
import numpy as np 
import tensorflow as tf 
import numpy as np
from tensorflow.python.keras import layers
from tensorflow.python.keras.layers import (Activation, AveragePooling2D,
                                            BatchNormalization, Conv2D, Conv3D,
                                            Dense, Flatten,
                                            GlobalAveragePooling2D,
                                            GlobalMaxPooling2D, Input,
                                            MaxPooling2D, MaxPooling3D,
                                            Reshape, Dropout, concatenate,
											UpSampling2D)
from tensorflow.python.keras.models import Model
from tensorflow.python.keras import backend as K_B
import coloredlogs
from os.path import exists
from input_data import input_data
import os
import time
# os.environ["CUDA_VISIBLE_DEVICES"]="0"

def edge_weights(flatten_image, rows , cols, std_intensity= 3.0, std_position = 1.0, radius=5):
	'''
	Inputs :
	flatten_image : 1 dim tf array of the row flattened image ( intensity is the average of the three channels) 
	std_intensity : standard deviation for intensity 
	std_position : standard devistion for position
	radius : the length of the around the pixel where the weights 
	is non-zero
	rows : rows of the original image (unflattened image)
	cols : cols of the original image (unflattened image)

	Output : 
	weights :  2d tf array edge weights in the pixel graph

	Used parameters :
	n : number of pixels 
	'''
	A = outer_product(flatten_image, tf.ones_like(flatten_image))
	A_T = tf.transpose(A)
	# print (A)
	intensity_weight = tf.exp(-1*tf.square((tf.realdiv((A - A_T), std_intensity))))

	xx, yy = tf.meshgrid(tf.range(rows), tf.range(cols))
	xx = tf.reshape(xx, (rows*cols,))
	yy = tf.reshape(yy, (rows*cols,))
	A_x = outer_product(xx, tf.ones_like(xx))
	A_y = outer_product(yy, tf.ones_like(yy))

	xi_xj = A_x - tf.transpose(A_x)
	yi_yj = A_y - tf.transpose(A_y)

	sq_distance_matrix = tf.square(xi_xj) + tf.square(yi_yj)
	sq_distance_matrix = tf.cast(sq_distance_matrix, tf.float32)
	dist_weight = tf.exp(-1*tf.realdiv(sq_distance_matrix,tf.square(std_position)))
	# dist_weight = tf.cast(dist_weight, tf.float32)
	print (dist_weight.get_shape())
	print (intensity_weight.get_shape())
	weight = tf.multiply(intensity_weight, dist_weight)


	# ele_diff = tf.reshape(ele_diff, (rows, cols))
	# w = ele_diff + distance_matrix
	'''
	for i in range(n):
		for j in range(n):
			# because a (x,y) in the original image responds in (x-1)*cols + (y+1) in the flatten image
			x_i= (i//cols) +1 
			y_i= (i%cols) - 1
			x_j= (j//cols) + 1
			y_j= (j%cols) - 1
			distance = np.sqrt((x_i - x_j)**2 + (y_i - y_j)**2)
			if (distance < radius):
				w[i][j] = tf.exp(-((flatten_image[i]- flatten_image[j])/std_intensity)**2) * tf.exp(-(distance/std_position)**2)
	# return w as a lookup table			
	'''
	return weight

def outer_product(v1,v2):
	'''
	Inputs:
	v1 : m*1 tf array
	v2 : m*1 tf array

	Output :
	v1 x v2 : m*m array
	'''
	v1 = tf.reshape(v1, (-1,))
	v2 = tf.reshape(v2, (-1,))
	v1 = tf.expand_dims((v1), axis=0)
	v2 = tf.expand_dims((v2), axis=0)
	return tf.matmul(tf.transpose(v1),(v2))

def numerator(k_class_prob,weights):

	'''
	Inputs :
	k_class_prob : k_class pixelwise probability (rows*cols) tensor 
	weights : edge weights n*n tensor 
	'''
	k_class_prob = tf.reshape(k_class_prob, (-1,))	
	return tf.reduce_sum(tf.multiply(weights,outer_product(k_class_prob,k_class_prob)))

def denominator(k_class_prob,weights):	
	'''
	Inputs:
	k_class_prob : k_class pixelwise probability (rows*cols) tensor
	weights : edge weights	n*n tensor 
	'''
	k_class_prob = tf.cast(k_class_prob, tf.float32)
	k_class_prob = tf.reshape(k_class_prob, (-1,))	
	return tf.reduce_sum(tf.multiply(weights,outer_product(k_class_prob,tf.ones(tf.shape(k_class_prob)))))

def soft_n_cut_loss(flatten_image,prob, k, rows, cols):
	'''
	Inputs: 
	prob : (rows*cols*k) tensor 
	k : number of classes (integer)
	flatten_image : 1 dim tf array of the row flattened image ( intensity is the average of the three channels)
	rows : number of the rows in the original image
	cols : number of the cols in the original image

	Output : 
	soft_n_cut_loss tensor for a single image

	'''

	soft_n_cut_loss = k
	weights = edge_weights(flatten_image, rows ,cols)
	
	for t in range(k): 
		soft_n_cut_loss = soft_n_cut_loss - (numerator(prob[:,:,t],weights)/denominator(prob[:,:,t],weights))

	return soft_n_cut_loss
	# return soft_n_cut_loss

if __name__ == '__main__':
	'''
	image = tf.ones([224*224])
	prob = tf.ones([224, 224,2])/2
	loss = soft_n_cut_loss(image, prob, 2, 224, 224)

	with tf.Session() as sess:
		init = tf.global_variables_initializer()
		sess.run(init)
		print(sess.run(loss))
		# print (sess.run(w))
 	'''
	img_rows = 64
	img_cols = 64
	num_classes = 16
	bn_axis=3
	display_step = 5
	recons_step = 5
	logdir = "checkpoints_multigpu/logs"
	checkpt_dir_ckpt = "checkpoints_multigpu/trained.ckpt"
	checkpt_dir = "checkpoints_multigpu"

	x = tf.placeholder(tf.float32, shape=[None, img_rows, img_cols, 3], name="input")
	global_step_tensor = tf.train.get_or_create_global_step()
	PS_OPS = [
    'Variable', 'VariableV2', 'AutoReloadVariable', 'MutableHashTable',
    'MutableHashTableOfTensors', 'MutableDenseHashTable'
	]
	def average_gradients(tower_grads):
		"""Calculate the average gradient for each shared variable across all towers.
		Note that this function provides a synchronization point across all towers.
		Args:
		tower_grads: List of lists of (gradient, variable) tuples. The outer list ranges
			over the devices. The inner list ranges over the different variables.
		Returns:
				List of pairs of (gradient, variable) where the gradient has been averaged
				across all towers.
		"""
		average_grads = []
		for grad_and_vars in zip(*tower_grads):

			# Note that each grad_and_vars looks like the following:
			#   ((grad0_gpu0, var0_gpu0), ... , (grad0_gpuN, var0_gpuN))
			grads = [g for g, _ in grad_and_vars]
			grad = tf.reduce_mean(grads, 0)

			# Keep in mind that the Variables are redundant because they are shared
			# across towers. So .. we will just return the first tower's pointer to
			# the Variable.
			v = grad_and_vars[0][1]
			grad_and_var = (grad, v)
			average_grads.append(grad_and_var)
		return average_grads

	def assign_to_device(device, ps_device):
		"""Returns a function to place variables on the ps_device.

		Args:
			device: Device for everything but variables
			ps_device: Device to put the variables on. Example values are /GPU:0 and /CPU:0.

		If ps_device is not set then the variables will be placed on the default device.
		The best device for shared varibles depends on the platform as well as the
		model. Start with CPU:0 and then test GPU:0 to see if there is an
		improvement.
		"""
		def _assign(op):
			node_def = op if isinstance(op, tf.NodeDef) else op.node_def
			if node_def.op in PS_OPS:
				return ps_device
			else:
				return device
		return _assign
	def create_parallel_optimization(model_fn, input_fn, optimizer,num_classes, controller="/cpu:0"):
		devices = ['/gpu:0', '/gpu:1']

		tower_grad_recons = []
		tower_grad_soft = []
		recons_loss_vec = []
		soft_loss_vec = []
		with tf.variable_scope(tf.get_variable_scope()) as outer_scope:
			for i, id in enumerate(devices):
				name = 'tower_{}'.format(i)
				

				with tf.device(assign_to_device(id, controller)), tf.name_scope(name):
					next_items = input_fn.get_next()
					output, decode, loss, recons_loss = create_wnet(next_items, num_classes, True)
					vars_encoder = [var for var in tf.trainable_variables() if var.name.startswith("ENCODER")]
					vars_trainable = [var for var in tf.trainable_variables()]
					with tf.name_scope('Compute_gradients'):
						soft_grads = optimizer.compute_gradients(loss, var_list=vars_encoder)
						recons_grads = optimizer.compute_gradients(recons_loss, var_list=vars_trainable)
					
					tower_grad_recons.append(recons_grads)
					tower_grad_soft.append(soft_grads)
					recons_loss_vec.append(recons_loss)
					soft_loss_vec.append(loss)
				outer_scope.reuse_variables()
		with tf.name_scope('apply_gradients'), tf.device(controller):
			recons_gradients = average_gradients(tower_grad_recons)
			soft_gradient = average_gradients(tower_grad_soft)
			apply_soft_op = optimizer.apply_gradients(soft_gradient, global_step_tensor)
			apply_recons_op = optimizer.apply_gradients(recons_gradients, global_step_tensor)
			avg_loss_recons = tf.reduce_mean(recons_loss_vec)
			avg_loss_soft = tf.reduce_mean(soft_loss_vec)
		return apply_recons_op, apply_soft_op, avg_loss_recons, avg_loss_soft

					
	def enc_conv_block(inputs, filters=[128,128], kernel_size=[3,3], activation='relu', kernel_initializer='he_normal', block='', module='', pre_pool=True):
		fa, fb = filters
		ka, kb = kernel_size
		conv1 = Conv2D(fa, ka, activation=activation, padding='same', kernel_initializer=kernel_initializer, name=module+'_conv_enc_'+block+'_1')(inputs)
		conv1 = Conv2D(fb, kb, activation=activation, padding='same', kernel_initializer=kernel_initializer, name=module+'_conv_enc_'+block+'_2')(conv1)
		conv1 = BatchNormalization(axis=bn_axis, name=module+'_bn_enc_'+block+'_3')(conv1)
		conv1 = Dropout(0.5,  name=module+'_dropout_enc_'+block)(conv1)
		pool1 = MaxPooling2D(pool_size=(2,2), name=module+'_maxpool_enc_'+block+'_4')(conv1)
		# tf.summary.histogram(module+'_maxpool_enc_'+block+'_4',pool1)
		if not pre_pool:
			return pool1
		else:
			return conv1,pool1

	def dec_conv_block(inputs, filters=[128, 128, 128], kernel_size=[2,3,3], activation='relu', kernel_initializer='he_normal', block='', module=''):
		previous_layer, concat_layer = inputs
		fa, fb, fc = filters
		ka, kb, kc = kernel_size
		up1 = Conv2D(fa, ka, activation=activation, padding='same', kernel_initializer=kernel_initializer, name=module+'_conv_dec_'+block+'_2')(UpSampling2D(size=(2,2), name=module+'_upsam_block_'+block+'_1')(previous_layer))
		# print (up1.get_shape())
		merge1 = concatenate([concat_layer, up1], name=module+'_concat_'+block+'_3')
		conv2 = Conv2D(fb, kb, activation=activation, padding='same', kernel_initializer=kernel_initializer, name=module+'_conv_dec_'+block+'_4')(merge1)
		conv3 = Conv2D(fc, kc, activation=activation, padding='same', kernel_initializer=kernel_initializer,name=module+'_conv_dec_'+block+'_5')(conv2)
		conv3 = Dropout(0.75, name=module+'_dropout_dec_'+block)(conv3)
		conv3 = BatchNormalization(axis=bn_axis, name=module+'_bn_dec_'+block+'_6')(conv3)
		# tf.summary.histogram(module+'_bn_dec_'+block+'_6', conv3)
		return conv3

	def join_enc_dec(inputs, filters=[1024,1024], kernel=[3,3],activation='relu', kernel_initializer='he_normal', module='', block='join'):	
		fa, fb = filters
		ka, kb = kernel
		conv1 = Conv2D(fa, ka, activation=activation, padding='same', kernel_initializer=kernel_initializer, name=module+"_join_conv_1")(inputs)
		conv1 = Conv2D(fb, kb, activation=activation, padding='same', kernel_initializer=kernel_initializer, name=module+"_join_conv_2")(conv1)
		conv1 = BatchNormalization(axis=bn_axis, name=module+'_join_bn_3_')(conv1)
		conv1 = Dropout(0.75, name=module+'_join_dropout_4')(conv1)
		# tf.summary.histogram(module+'_join_bn_3_', conv1)
		return conv1
	def loss_functions(x, output, decode):
		soft_map = (x, output)
		loss = tf.map_fn(lambda x:soft_n_cut_loss( tf.reshape(tf.image.rgb_to_grayscale(x[0]), (img_rows*img_cols,)), tf.reshape(x[1], (img_rows, img_cols, num_classes)), num_classes, img_rows, img_cols), soft_map, dtype=x.dtype)
		loss = tf.reduce_mean(loss)
		recons_map = (x, decode)
		recons_loss = tf.map_fn(lambda x: tf.reduce_mean(tf.square(x[0] - x[1])), recons_map, dtype=x.dtype)
		recons_loss = tf.reduce_mean(recons_loss)
		return loss, recons_loss
	def unet(input_size=(-1,img_rows,img_cols,3), input_tensor=None, output_layers=1,module=''):
		
		if input_tensor is None:
			inputs = Input(input_size)
		else:
			inputs = input_tensor
		bn_axis=3
		with tf.variable_scope(module+'_Encoder'):
			prepool_1, layer1 = enc_conv_block(inputs, [64, 64], [3,3], block='a', module=module)
			prepool_2, layer2 = enc_conv_block(layer1, [128,128], [3,3], block='b', module=module)
			prepool_3, layer3 = enc_conv_block(layer2, [256,256], [3,3], block='c', module=module)
			prepool_4, layer4 = enc_conv_block(layer3, [512,512], [3,3], block='d', module=module)

			layer4 = Dropout(0.7)(layer4)

			join_layer = join_enc_dec(layer4, [1024,1024], [3,3], module=module)
		with tf.variable_scope(module+'_Decoder'):
			layer4 = dec_conv_block([join_layer, prepool_4], [512,512,512], [2,3,3], block='d', module=module)
			layer3 = dec_conv_block([layer4, prepool_3], [256,256,256], [2,3,3], block='c', module=module)
			layer2 = dec_conv_block([layer3, prepool_2], [128,128,128], [2,3,3], block='b', module=module)
			layer1 = dec_conv_block([layer2, prepool_1], [64,64,64], [2,3,3], block='a', module=module)

			output = Conv2D(output_layers, 1, kernel_initializer='he_normal', name=module+'_output_layer')(layer1)

		return output

	def encoder(num_classes, input_shape=[-1,img_rows,img_cols,3], input_tensor = None):
		if input_tensor is None:
			img_input = Input(shape=input_shape)
		else:
			img_input = input_tensor
		x = unet(input_tensor = img_input, output_layers=num_classes, module='ENCODER')
		x = tf.nn.softmax(x, axis=3)
		return (x)
	def decoder(input_shape=[-1, img_rows,img_cols,3], input_tensor=None):
		if input_tensor is None:
			img_input = Input(shape=input_shape)
		else:
			img_input = input_tensor
		x = unet(input_tensor = img_input, output_layers=3, module='DECODER') # 3 because  of number of channels
		return (x)
	
	coloredlogs.install(level='DEBUG')
	tf.logging.set_verbosity(tf.logging.DEBUG)
	def create_wnet(input_tensor, num_classes, loss_need=False):
		print (num_classes)
		output = encoder(num_classes, input_tensor = input_tensor)
		decode = decoder(input_tensor=output)
		with tf.name_scope('Images'):
	
			output_flatten = tf.reshape(output, (-1, img_rows*img_cols, num_classes))
			colormap = tf.reshape(tf.linspace(0.0, 255.0, num_classes), (num_classes, -1))
			image_segmented = tf.map_fn(lambda x: tf.reshape(tf.matmul(x, colormap), (img_rows, img_cols, 1)), output_flatten, dtype=output_flatten.dtype)

			tf.summary.image('Input', input_tensor)
			tf.summary.image('Segmented', image_segmented)
			tf.summary.image('Reconstruction', decode)

		if loss_need:
			loss, recons_loss = loss_functions(input_tensor, output, decode)
			return output, decode, loss, recons_loss
		else:
			return output, decode
	

	iterator = input_data()
	
	start_learning_rate =5e-6#0.000001
	lr = tf.train.exponential_decay(start_learning_rate, global_step_tensor, 5000, 0.975, staircase=True)
	tf.summary.scalar('Learning_Rate', lr)
	optimizer = tf.train.AdamOptimizer(learning_rate=lr)
	apply_recons_op, apply_soft_op, avg_loss_recons, avg_loss_soft = create_parallel_optimization(create_wnet, iterator, optimizer, num_classes)
	with tf.name_scope('Loss'):
		tf.summary.scalar('Reconstruction_Loss', avg_loss_recons)
		tf.summary.scalar('Soft_N_Cut_Loss', avg_loss_soft)
	merged = tf.summary.merge_all()
	saver = tf.train.Saver()
	sess = tf.Session(config=tf.ConfigProto(allow_soft_placement=True))
	K_B.set_session(sess)
	with K_B.get_session() as sess:
		train_writer = tf.summary.FileWriter(logdir,sess.graph)
		
		init = tf.global_variables_initializer()
		sess.run(init)
		
		if exists(checkpt_dir):
			if tf.train.latest_checkpoint(checkpt_dir) is not None:
				tf.logging.info('Loading Checkpoint from '+ tf.train.latest_checkpoint(checkpt_dir))
				saver.restore(sess, tf.train.latest_checkpoint(checkpt_dir))
		else:
			tf.logging.info('Training from Scratch -  No Checkpoint found')
		
		# img_lab = np.expand_dims(cv2.cvtColor(img, cv2.COLOR_BGR2LAB), axis=0)
		i = 0
	
		times = []
		learning_rate = sess.run(lr)
		tf.logging.info('Learning Rate: ' + str(learning_rate))

		while True:
			start = time.time()
			# for _ in range(recons_step):
			_= sess.run([apply_soft_op])
			_= sess.run([apply_recons_op])
			# print (batch_x)
			times.append(time.time() - start)
			i+=1
			if i%(display_step*recons_step) ==0:
				average_reconstruction_loss, average_softncut_loss, summary, gst = sess.run([avg_loss_recons, avg_loss_soft, merged, global_step_tensor])
				train_writer.add_summary(summary, gst)
				tf.logging.info("Iteration: " + str(gst) + " Soft N-Cut Loss: " + str(average_softncut_loss) + " Reconstruction Loss " + str(average_reconstruction_loss) + " Time " + str(np.mean(times)))
				# print (segment.max())
				# print (segment.min())
				del times[:]
				saver.save(sess, checkpt_dir_ckpt, global_step=tf.train.get_global_step())