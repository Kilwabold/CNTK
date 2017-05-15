import h5py
import numpy as np

from . import UserMinibatchSource,StreamInformation,MinibatchData
from .. import Value, sequence
from ..device import use_default_device


#TODO: support sparse tensors
class H5DataSource(UserMinibatchSource):
    """
    Data Source on HDF5 data. For more information on HDF5 format and the Python API: h5py, please see: http://docs.h5py.org/en/latest/index.html.

    Args:
        datagroup (a h5py datagroup or a `dictionary`): data group which is the source of the minibatch. It can be either 
          an h5py data group which is composed of a dictionary of h5py dataset, 2) a Python dictionary mapping from name to 
          h5py dataset. If a dataset is a sequence, it must be a variable length 1-d array (see https://support.hdfgroup.org/HDF5/doc1.6/UG/11_Datatypes.html);
          it is required to set three attributes: 1) dataset.attrs['is_seq']: bool; 2) dataset.attrs['shape']: a tuple or list of integers which defines the element data 
          shape; 3) dataset.attrs['dtype']: numpy element datatype string (see https://docs.scipy.org/doc/numpy/reference/arrays.dtypes.html), e.g.:  'i4': 32-bit 
          signed integer; 'f4':   32-bit floating-point number.
        device (`DeviceDescriptor`, defaults to `None`): CNTK DeviceDescriptor; default  is  use_default_device().

    Example:
     >>> with h5py.File("my-test-data.h5", "w") as f:
     ...    #sequence lenth is fixed to 3; element shape is (4, )
     ...    fixed_len_seq = f.create_dataset("fixed_len_seq", (100, 3, 4), dtype=np.float32)
     ...    fixed_len_seq.attrs['is_seq'] = True
     ...    fixed_len_seq.attrs['shape'] = (4,)
     ...    fixed_len_seq.attrs['dtype'] = 'f4' #float32
     ... 
     ...    vlen_seq = f.create_dataset("vlen_seq", 
     ...                 (100, ), 
     ...                 h5py.special_dtype(vlen=np.dtype('f4'))
     ...                 #or using:                
     ...                 #h5py.special_dtype(vlen=np.float32)
     ...                               )
     ...    vlen_seq.attrs['is_seq'] = True
     ...    vlen_seq.attrs['shape'] = (3,)
     ...    vlen_seq.attrs['dtype'] = 'f4' #float32
     ...    label = f.create_dataset("label", (100,1), dtype=np.float32) 
     ...    #create fake data
     ...    for i in range(100):
     ...         fixed_len_seq[i] = np.ones((3, 4)) * i
     ...         #vlen is between 3 and 10
     ...         vlen_seq_inshape = np.ones((np.random.randint(3,10), 3)) * i
     ...         vlen_seq[i] = np.reshape(vlen_seq_inshape, np.prod(vlen_seq_inshape.shape))
     ...         label[i] = np.array([i])
     ...    #use the dataset with H5DataSource    
     ...    #ds = H5DataSource(f) # or dictionary to specify which fields to use as datasource: 
     ...    ds = H5DataSource({'fixed_len_seq': f['fixed_len_seq'], 'label': f['label'], 'vlen_seq': f['vlen_seq']})
     ...    #try minibatch
     ...    batch_size = 30
     ...    for i in range( (100 // batch_size) + 2):
     ...         batch = ds.next_minibatch(batch_size)
     ...         v_fixed_seq = C.sequence.input(4)
     ...         fixed_seq_op_result = (C.sequence.last(v_fixed_seq) * 1).eval({v_fixed_seq: batch[ds.stream_info_mapping['fixed_len_seq']]})
     ...         print(fixed_seq_op_result)
     ...         v_vlen_seq = C.sequence.input(3)
     ...         v_vlen_seq_op_result = (C.sequence.last(v_vlen_seq) * 1).eval({v_vlen_seq: batch[ds.stream_info_mapping['vlen_seq']]})
     ...         print(v_vlen_seq_op_result)
     ...         v_label = C.input(1)
     ...         v_label_op_result = (v_label * 1).eval({v_label: batch[ds.stream_info_mapping['label']]})
     ...         print(v_label_op_result)
     ...    f.close()
     """
    def __init__(self, datagroup, device = use_default_device()):

        self.device = device
        stream_infos = {}
        for i, name in enumerate(datagroup):
            # or storage_format = 'sparse' # to be implemented later
            storage_format = 'dense'
            dataset = datagroup[name]
            is_seq = 'is_seq' in dataset.attrs and dataset.attrs['is_seq']

            if is_seq:
                # If variable length data, only 1d-array can be stored in H5:
                # http://stackoverflow.com/questions/42658438/storing-multidimensional-variable-length-array-with-h5py
                dshape = self.dataset_shape_(dataset)
                dtype = np.dtype(dataset.attrs['dtype'])
            else:
                dshape = dataset.shape[1:]
                dtype = dataset.dtype
            num_data_points = dataset.shape[0]

            strm_info = StreamInformation(name, i, storage_format, dtype, dshape)
            stream_infos[name] = strm_info
        self.datagroup = datagroup
        self.stream_info_mapping = stream_infos

        self.num_data_points = num_data_points
        self.next_idx = 0

        super(H5DataSource, self).__init__()

    def dataset_shape_(self, dataset):
        dshape = dataset.attrs['shape']
        if type(dshape) is not np.ndarray:
            dshape = np.array([dshape])
        dshape = dshape.tolist()
        return dshape

    def stream_infos(self):
        return self.stream_info_mapping.values()

    def sample_count_(self, dataset, data_idx):
        is_seq = 'is_seq' in dataset.attrs and dataset.attrs['is_seq']
        if is_seq:
            elm_shape = dataset.attrs['shape']
            if type(elm_shape) is not np.ndarray:
                elm_shape = np.array([elm_shape])
            elm_size = np.prod(elm_shape)
            seq_size = np.prod(dataset[data_idx].shape)
            seq_len = seq_size // elm_size
            return seq_len
        else:
            return 1

    def max_seq_len_(self, idx):
        mx_len = 0
        for name in self.datagroup:
            strm_info = self.stream_info_mapping[name]
            dataset = self.datagroup[name]
            mx_len = max(mx_len, self.sample_count_(dataset, idx))
        return mx_len

    def next_minibatch(self, num_samples_in_batch, number_of_workers=1, worker_rank=0, device=None):
        # Note that in this example we do not yet make use of number_of_workers or
        # worker_rank, which will limit the minibatch source to single GPU / single node
        # scenarios.
        # TODO: check self.num_samples > num_samples
        mydevice = device if device else self.device
        start = self.next_idx
        end = start
        sample_count = 0
        num_seq = 0
        while sample_count < num_samples_in_batch:
            max_seq_len = self.max_seq_len_(end)
            sample_count = sample_count + max_seq_len
            num_seq = num_seq + 1
            end = (end + 1) % self.num_data_points
            if sample_count > num_samples_in_batch:
                break
        self.next_idx = end

        sweep_end = False if end > start else True

        result = {}
        for name in self.datagroup:
            strm_info = self.stream_info_mapping[name]
            dataset = self.datagroup[name]

            if sweep_end:
                # cyclic back to the starting of the dataset
                data = np.concatenate([dataset[start:self.num_data_points], dataset[0:end]])
            else:
                data = dataset[start:end]

            is_seq = 'is_seq' in dataset.attrs and dataset.attrs['is_seq']
            if is_seq:
                elm_shape = dataset.attrs['shape']
                if type(elm_shape) is not np.ndarray:
                    elm_shape = np.array([elm_shape])
                elm_size = np.prod(elm_shape)
                elm_shape = elm_shape.tolist()
                raw_data = data
                res_data = []
                sample_count = 0
                for seq in data:
                    seq_size = np.prod(seq.shape)
                    seq_len = seq_size // elm_size
                    # Because HDF5 only support 1-D variable length array,
                    # it has to be reshaped to meet the CNTK shape requirement
                    dshape = [seq_len] + elm_shape
                    res_data.append(np.reshape(seq, dshape))
                    sample_count = sample_count + seq_len

                data = Value.create(var=sequence.input_variable(shape=elm_shape), data=res_data, device=mydevice)
                sample_count = int(sample_count)
            else:
                sample_count = num_seq
                dshape = dataset.shape[1:]
                data = Value.create(var=sequence.input_variable(shape=dshape), data=data, device=mydevice)

            batch_data = MinibatchData(data, num_seq, sample_count, sweep_end)
            result[strm_info] = batch_data
        return result