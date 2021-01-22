"""
ReactionModel class

This is a big wrapper class for most of the KIPET methods
"""
# Standard library imports
import collections
import copy
import pathlib
import weakref

# Third party imports
import numpy as np
import pandas as pd
from pyomo.environ import Var
from pyomo.core.base.var import IndexedVar
from pyomo.dae.diffvar import DerivativeVar

# Kipet library imports
import kipet.library.core_methods.data_tools as data_tools
from kipet.library.core_methods.EstimationPotential import (
    rhps_method,
    replace_non_estimable_parameters,
    )
# from kipet.library.core_methods.EstimationPotential_working import (
#     reduce_model,
#    )
from kipet.library.core_methods.EstimabilityAnalyzer import EstimabilityAnalyzer
from kipet.library.core_methods.FESimulator import FESimulator
# from kipet.library.core_methods.MEE_new import MultipleExperimentsEstimator
from kipet.library.core_methods.ParameterEstimator import ParameterEstimator
from kipet.library.core_methods.PyomoSimulator import PyomoSimulator
from kipet.library.core_methods.TemplateBuilder import TemplateBuilder
from kipet.library.core_methods.VarianceEstimator import VarianceEstimator

from kipet.library.common.pre_process_tools import decrease_wavelengths
from kipet.library.common.pyomo_model_tools import get_vars
from kipet.library.dev_tools.display import Print

#from kipet.library.common.read_write_tools import set_directory
from kipet.library.post_model_build.scaling import scale_models
from kipet.library.post_model_build.replacement import ParameterReplacer

from kipet.library.mixins.TopLevelMixins import WavelengthSelectionMixins

from kipet.library.top_level.datahandler import DataBlock, DataSet
from kipet.library.top_level.helper import DosingPoint
from kipet.library.top_level.model_components import ParameterBlock, ComponentBlock
from kipet.library.top_level.settings import Settings, USER_DEFINED_SETTINGS
from kipet.library.top_level.clean import remove_file


DEBUG=False
_print = Print(verbose=DEBUG)

class ReactionModel(WavelengthSelectionMixins):
    
    """This should consolidate all of the Kipet classes into a single class to
    enable a simpler framework for using the software. 
    
    """
    def __init__(self, *args, **kwargs):
        
        self.name = kwargs.get('name', 'Model-1')
        self.model = None
        self.builder = TemplateBuilder()
        self.components = ComponentBlock()   
        self.parameters = ParameterBlock()
        self.datasets = DataBlock()
        self.constants = None
        self.results_dict = {}
        self.settings = Settings(category='model')
        self.algebraic_variables = []
        
        self.variances = {}
        
        self.odes = None
        self.algs = None
        self.custom_objective = None
        self.optimized = False
        
        self.dosing_var = None
        self.dosing_points = None
        self._has_dosing_points = False
        
        self._has_non_absorbing_species = False
        
        self._var_to_fix_from_trajectory = []
        self._var_to_initialize_from_trajectory = []

    def __repr__(self):
        
        # m = 20
        
        # kipet_str = f'ReactionModel Object {self.name}:\n\n'
        # kipet_str += f'{"ODEs".rjust(m)} : {hasattr(self, "odes") and getattr(self, "odes") is not None}\n'
        # kipet_str += f'{"Algebraics".rjust(m)} : {hasattr(self, "odes") and getattr(self, "odes") is not None}\n'
        # kipet_str += f'{"Model".rjust(m)} : {hasattr(self, "model") and getattr(self, "model") is not None}\n'
        # kipet_str += '\n'
        
        # kipet_str += f'{self.components}\n'
        # kipet_str += f'Algebraic Variables:\n{", ".join([str(av) for av in self.algebraic_variables])}\n\n'
        # kipet_str += f'{self.parameters}\n'
        # kipet_str += f'{self.datasets}\n'
        
        return f'KipetModel {self.name}'#kipet_str
    
    def __str__(self):
        
        m = 20
        
        kipet_str = f'ReactionModel Object {self.name}:\n\n'
        kipet_str += f'{"ODEs".rjust(m)} : {hasattr(self, "odes") and getattr(self, "odes") is not None}\n'
        kipet_str += f'{"Algebraics".rjust(m)} : {hasattr(self, "odes") and getattr(self, "odes") is not None}\n'
        kipet_str += f'{"Model".rjust(m)} : {hasattr(self, "model") and getattr(self, "model") is not None}\n'
        kipet_str += '\n'
        
        kipet_str += f'{self.components}\n'
        kipet_str += f'Algebraic Variables:\n{", ".join([str(av) for av in self.algebraic_variables])}\n\n'
        kipet_str += f'{self.parameters}\n'
        kipet_str += f'{self.datasets}\n'
    
        return kipet_str
    
    def _unwanted_G_initialization(self, *args, **kwargs):
        """Prepare the ParameterEstimator model for unwanted G contributions
        
        """
        self.builder.add_qr_bounds_init(bounds=(0,None),init=1.1)
        self.builder.add_g_bounds_init(bounds=(0,None))
        
        return None
    
    def add_dosing_point(self, component, time, step):
        """Add a dosing point or several (check template for how this is handled)
        
        """
        conversion_dict = {'state': 'X', 
                           'concentration': 'Z',
                           }
        
        if self.dosing_var is None:
            raise AttributeError('ReactionModel needs a designated algebraic variable for dosing')
        
        if component not in self.components.names:
            raise ValueError('Invalid component name')
        
        dosing_point = DosingPoint(component, time, step)

        model_var = conversion_dict[self.components[component].state]
        
        if self.dosing_points is None:
            self.dosing_points = {}
        
        if model_var not in self.dosing_points.keys():
            self.dosing_points[model_var] = [dosing_point]
        else:
            self.dosing_points[model_var].append(dosing_point)
            
        self._has_dosing_points = True
        
    def set_dosing_var(self, var):
        
        """Check when multiple dosing vars are needed"""
        
        # if not isinstance(var, list):
        #     var = [var]
        
        # for _var in var:
        if var not in self.algebraic_variables:
            raise ValueError('Not a valid algebraic variable')
            
        self.dosing_var = var

        return None
    
    def call_fe_factory(self):
        """Somewhat of a wrapper for this simulator method, but better"""

        self.simulator.call_fe_factory({'Y': [self.dosing_var]}, self.dosing_points)
        
        return None
    
    def clone(self, *args, **kwargs):
        """Makes a copy of the ReactionModel and removes the data. This is done
        to reuse the model, components, and parameters in an easier manner
        
        """
        new_kipet_model = copy.deepcopy(self)
        
        name = kwargs.get('name', self.name + '_copy')
        copy_model = kwargs.get('model', True)
        copy_builder = kwargs.get('builder', True)
        copy_components = kwargs.get('components', True)   
        copy_parameters = kwargs.get('parameters', True)
        copy_datasets = kwargs.get('datasets', True)
        copy_constants = kwargs.get('constants', True)
        copy_settings = kwargs.get('settings', True)
        copy_algebraic_variables = kwargs.get('alg_vars', True)
        copy_odes = kwargs.get('odes', True)
        copy_algs = kwargs.get('algs', True)
        
        # Reset the datasets
        
        new_kipet_model.name = name
        
        if not copy_model:
            new_kipet_model.model = None
        
        if not copy_builder:
            new_kipet_model.builder = TemplateBuilder()
            
        if not copy_components:
            new_kipet_model.components = ComponentBlock()
        
        if not copy_parameters:
            new_kipet_model.parameters = ParameterBlock()
            
        if not copy_datasets:
            del new_kipet_model.datasets
            new_kipet_model.datasets = DataBlock()
            
        if not copy_constants:
            new_kipet_model.constants = None
            
        if not copy_algebraic_variables:
            new_kipet_model.algebraic_variables = []
            
        if not copy_settings:
            new_kipet_model.settings = Settings()
            
        if not copy_odes:
            new_kipet_model.odes = None
            
        if not copy_algs:
            new_kipet_model.algs = None
        
        list_of_attr_to_delete = ['p_model', 'v_model', 'p_estimator',
                                  'v_estimator', 'simulator']
        
        for attr in list_of_attr_to_delete:
            if hasattr(new_kipet_model, attr):
                setattr(new_kipet_model, attr, None)
        
        new_kipet_model.results_dict = {}
            
        return new_kipet_model
        
    def add_component(self, *args, **kwargs):
        """Add the components to the Kipet instance
        
        Args:
            components (list): list of Component instances
            
        Returns:
            None
            
        """
        self.components.add_component(*args, **kwargs)
        return None
    
    def add_parameter(self, *args, **kwargs):
        """Add the parameters to the Kipet instance
        
        Args:
            parameters (list): list of Parameter instances
            
            factor (float): defaults to 1, the scalar multiple of the parameters
            for simulation purposes
            
        Returns:
            None
            
        """
        self.parameters.add_parameter(*args, **kwargs)
        return None
    
    def add_dataset(self, *args, **kwargs):
        """Add the datasets to the Kipet instance
        
        Args:
            datasets (list): list of Parameter instances
            
            factor (float): defaults to 1, the scalar multiple of the parameters
            for simulation purposes
            
        Returns:
            None
            
        """
        name = kwargs.get('name', None)
        if len(args) > 0:
            name = args[0]
        filename = kwargs.get('file', None)
        data = kwargs.pop('data', None)
        category = kwargs.get('category', None)
        
        # Check if file name is given and add directory (general)
        if filename is not None:
            filename = _set_directory(self, filename)
            kwargs['file'] = filename
            #kwargs['data'] = None
            
            # Read data from file
            dataframe = data_tools.read_file(filename)
        
        elif filename is None and data is not None:
            dataframe = data
        
        else:
            raise ValueError('User must provide filename or dataframe')
        
        # Now we have the dataframe of data - check labels for components
        if category is None:
            self._check_data_category(name, dataframe, **kwargs)    
        else:
            self._add_categorized_dataset(name, dataframe, **kwargs)
        
        return None
    
    def _check_data_category(self, name, data, **kwargs):
        """Checks the category for data entered without a category"""
        
        # if components have already been entered, check them
        if len(self.components) > 0:
            data_labels = []
            
            # The types of data that can be autormated (concentration and state)
            concentration_data_labels = []
            state_data_labels = []

            for col in data.columns:
                if col in self.components.names:
                    if self.components[col].state == 'concentration':
                        concentration_data_labels.append(col)
                    elif self.components[col].state == 'state':
                        state_data_labels.append(col)
                        
            if len(concentration_data_labels) > 0:
                state_data = data.loc[:, concentration_data_labels]
                df_name = name if name is not None else 'C_data'
                self.datasets.add_dataset(df_name, category='concentration', data=state_data)
                
            if len(state_data_labels) > 0:
                state_data = data.loc[:, state_data_labels]
                df_name = name if name is not None else 'U_data'
                self.datasets.add_dataset(df_name, category='state', data=state_data)

        else:
            raise AttributeError('Data must have a cateogory or be matched to component data')
            
        remove_negatives = kwargs.get('remove_negatives', False)
        if remove_negatives:
            self.datasets[df_name].remove_negatives()
            
        return None
    
    def _add_categorized_dataset(self, name, data, **kwargs):
        """Specific function for adding concentration data"""
        
        category = kwargs.get('category', None)

        # General trajectory data
        if category == 'trajectory':
            df_name = name if name is not None else 'Traj_data'
            self.datasets.add_dataset(df_name, category=category, data=data)
        elif category == 'concentration':
            df_name = name if name is not None else 'C_data'
            self.datasets.add_dataset(df_name, category=category, data=data)
        elif category == 'state':
            df_name = name if name is not None else 'U_data'
            self.datasets.add_dataset(df_name, category=category, data=data)
        elif category == 'spectral':
            df_name = name if name is not None else 'D_data'
            self.datasets.add_dataset(df_name, category=category, data=data)
        else:
            df_name = name if name is not None else 'UD_data'
            self.datasets.add_dataset(df_name, category='custom', data=data)
                
        remove_negatives = kwargs.get('remove_negatives', False)
        if remove_negatives:
            self.datasets[df_name].remove_negatives()
        
        return None
    
    def add_algebraic_variables(self, *args, **kwargs):
        
        if isinstance(args[0], list):
            self.algebraic_variables = args[0]
        self.builder.add_algebraic_variable(*args, **kwargs)
        return None
    
    def set_times(self, start_time=None, end_time=None):
        """Add times to model for simulation (overrides data-based times)"""
        
        if start_time is None or end_time is None:
            raise ValueError('Time needs to be a number')
        
        self.settings.general.simulation_times = (start_time, end_time)
        return None
    
    # def set_directory(self, filename, abs_dir=False):
    #     """Wrapper for the set_directory method. This replaces the awkward way
    #     of ensuring the correct directory for the data is used."""

    #     directory = self.settings.general.data_directory
    #     print(f'The current data directory is : {directory}')
    #     file_path = pathlib.Path(directory).joinpath(filename)
    #     print(f'The data file is the following: {file_path}')
        
    #     return file_path
    
    # def write_file(self, filename, data, directory=None, filetype='csv'):
    #     """Method to write data to a file using KipetModel
    #     """
    #     _filename = filename
        
    #     if directory is None:
    #         _filename = self.set_directory(filename)
    #     else:
    #         _filename = pathlib.Path(directory).joinpath(filename)
        
    #     data_tools.write_file(_filename, data, filetype)
        
    #     return None
        
    # def read_data_file(self, filename, directory=None):
    #     """Method to read data file using KipetModel
    #     """
    #     _filename = filename
        
    #     if directory is None:
    #         _filename = self.set_directory(filename)
    #     else:
    #         _filename = pathlib.Path(directory).joinpath(filename)
        
    #     return data_tools.read_file(_filename)
    
    def add_equations(self, ode_fun):
        """Wrapper for the set_odes method used in the builder"""
        
        self.odes = ode_fun
        return None
    
    def add_algebraics(self, algebraics):
        """Wrapper for the set_algebraics method used in the builder"""
        
        self.algs = algebraics
        return None
    
    def add_objective_from_algebraic(self, algebraic_var):
        """Wrapper for the set_algebraics method used in the builder"""
        
        self.custom_objective = algebraic_var
        return None
    
    def populate_template(self, *args, **kwargs):
        
        if len(self.components) > 0:
            self.builder.add_components(self.components)
        else:
            raise ValueError('The model has no components')
            
        if len(self.parameters) > 0:
            self.builder.add_parameters(self.parameters)
        else:
            self.allow_optimization = False   
        
        if len(self.datasets) > 0:
            self.builder.input_data(self.datasets)
            self.allow_optimization = True
        elif len(self.datasets) == 0:
            self.allow_optimization = False
        else:
            pass
            
        if hasattr(self, 'odes') and self.odes is not None:
            self.builder.set_odes_rule(self.odes)
        else:
            raise ValueError('The model requires a set of ODEs')
            
        if hasattr(self, 'algs') and self.algs is not None:
            self.builder.set_algebraics_rule(self.algs)
            
        if hasattr(self, 'custom_objective') and self.custom_objective is not None:
            self.builder.set_objective_rule(self.custom_objective)
        
        self.builder.set_parameter_scaling(self.settings.general.scale_parameters)
        self.builder.add_state_variance(self.components.variances)
        
        if self._has_dosing_points:
            self._add_feed_times()
            
        # It seems this is repetitive - refactor
        self.builder._G_contribution = self.settings.parameter_estimator.G_contribution
        
        if self.settings.parameter_estimator.G_contribution is not None:
            self._unwanted_G_initialization()
        
        start_time, end_time = None, None
        if self.settings.general.simulation_times is not None:
            #print(f'times are: {type(self.settings.general.simulation_times)}')
            start_time, end_time = self.settings.general.simulation_times
       
        return start_time, end_time
        
    def create_pyomo_model(self, *args, **kwargs):
        """Adds the component, parameter, data, and odes to the TemplateBuilder
        instance and creates the model. The model is stored under self.model
        and there is nothing returned.

        Args:
            None

        Returns:
            None

        """
        if hasattr(self, 'model'):
            del self.model
            
        start_time, end_time = self.populate_template(*args, **kwargs)
        self.model = self.builder.create_pyomo_model(start_time, end_time)
        
        if self._has_non_absorbing_species:
            self.builder.set_non_absorbing_species(self.model, self.non_abs_list, check=True)    
        
        if hasattr(self,'fixed_params') and len(self.fixed_params) > 0:
            for param in self.fixed_params:
                self.model.P[param].fix()
            
        return None
    
    def _add_feed_times(self):
        
        feed_times = set()
        
        for model_var, dp in self.dosing_points.items():
            for point in dp:
                feed_times.add(point.time)
        
        self.builder.add_feed_times(list(feed_times))
        return None
    
    def _from_trajectories(self, estimator):
        """This handles all of the fixing, initializing, and scaling from 
        trajectory data
        
        """
        if len(self._var_to_fix_from_trajectory) > 0:
            for fix in self._var_to_fix_from_trajectory:
                if isinstance(fix[2], str):
                    if fix[2] in self.datasets.names:
                        fix[2] = self.datasets[fix[2]].data
                getattr(self, estimator).fix_from_trajectory(*fix)
                
        if len(self._var_to_initialize_from_trajectory) > 0:
            for init in self._var_to_initialize_from_trajectory:
                if isinstance(init[1], str):
                    if init[1] in self.datasets.names:
                        init[1] = self.datasets[init[1]].data
                getattr(self, estimator).initialize_from_trajectory(*init)
                
        return None
    
    def simulate(self):
        """This should try to handle all of the simulation cases"""
    
        # Create the simulator object
        self.create_simulator()
        # Add any previous trajectories, if given
        self._from_trajectories('simulator')
        # Run the simulation
        self.run_simulation()
        
        return None
    
    def create_simulator(self):
        """This should try to handle all of the simulation cases"""
        
        _print('Setting up simulator:')
        sim_set_up_options = copy.copy(self.settings.simulator)
        _print(sim_set_up_options)
        dis_method = sim_set_up_options.pop('method', 'dae.collocation')
        
        if self.dosing_var is not None:
            dis_method = 'fe'
        
        # kwargs = self.settings.collocation
        
        # method = self.settings.collocation.method
        
        # method = kwargs.get('method', 'dae.collocation')
        # ncp = kwargs.get('ncp', 3)
        # nfe = kwargs.get('nfe', 50)
        # scheme = kwargs.get('scheme', 'LAGRANGE-RADAU')
        
        _print(dis_method)
        
        if dis_method == 'fe':
            simulation_class = FESimulator
        else:
            simulation_class = PyomoSimulator
        
        if self.model is None:
            self.create_pyomo_model(*self.settings.general.simulation_times)
        
        self.s_model = self.model.clone()
        
        # components_to_delete = ['Cm', 'U'] 
        # for comp in components_to_delete:
        #     if hasattr(self.s_model, comp):
        #         self.s_model.del_component(comp)
        
        for param in self.s_model.P.values():
            param.fix()
        
        print(simulation_class)
        
        simulator = simulation_class(self.s_model)
        simulator.apply_discretization(self.settings.collocation.method,
                                       ncp=self.settings.collocation.ncp,
                                       nfe=self.settings.collocation.nfe,
                                       scheme=self.settings.collocation.scheme)
        
        if self.dosing_var is not None and hasattr(self.s_model, 'Y'):
            for key in simulator.model.alltime.value:
                simulator.model.Y[key, self.dosing_var].set_value(key)
                simulator.model.Y[key, self.dosing_var].fix()
        
        self.simulator = simulator
        print('Finished creating simulator')
        
        return None
        
    def run_simulation(self):
        """Runs the simulations, may be combined with the above at a later date
        
        """
        if self._has_dosing_points:
            self.call_fe_factory()
        
        simulator_options = self.settings.simulator
        simulator_options.pop('method', None)
        self.results = self.simulator.run_sim(**simulator_options)
        self.results.file_dir = self.settings.general.charts_directory
    
        return None
    
    def reduce_spectra_data_set(self, dropout=4):
        """To reduce the computational burden, this can be used to reduce 
        the amount of spectral data used
        
        """
        A_set = [l for i, l in enumerate(self.model.meas_lambdas) if (i % dropout == 0)]
        return A_set
    
    def bound_profile(self, var, bounds):
        """Wrapper for TemplateBuilder bound_profile method"""
        
        self.builder.bound_profile(var=var, bounds=bounds)
        return None
    
    def create_variance_estimator(self, **kwargs):
        """This is a wrapper for creating the VarianceEstimator"""
        if len(kwargs) == 0:
            kwargs = self.settings.collocation
        
        if self.model is None:    
            self.create_pyomo_model()  
        
        self.create_estimator(estimator='v_estimator', **kwargs)
        self._from_trajectories('v_estimator')
        return None
        
    def create_parameter_estimator(self, **kwargs):
        """This is a wrapper for creating the ParameterEstiamtor"""
        if len(kwargs) == 0:
            kwargs = self.settings.collocation
            
        if self.model is None:    
            self.create_pyomo_model()  
            
        self.create_estimator(estimator='p_estimator', **kwargs)
        self._from_trajectories('p_estimator')
        return None
        
    def iniitalize_from_simulation(self, estimator='p_estimator'):
        
        if not hasattr(self, 's_model'):
            _print('Starting simulation for initialization')
            self.simulate()
            _print('Finished simulation, updating variables...')

        _print(f'The model has the following variables:\n{get_vars(self.s_model)}')
        
        vars_to_init = ['Z', 'dZdt', 'X', 'dXdt'] #get_vars(self.model)
        vars_to_init = get_vars(self.s_model)
        
        
        _print(vars_to_init)
        for var in vars_to_init:
            if hasattr(self.results, var):    
                _print(f'Updating variable: {var}')
                getattr(self, estimator).initialize_from_trajectory(var, getattr(self.results, var))
            else:
                continue
        
        return None
    
    def create_estimator(self, estimator=None, **kwargs):
        """This function handles creating the Estimator object"""
        
        # if not self.allow_optimization:
        #     raise AttributeError('This model is not ready for optimization')
        
        # method = kwargs.pop('method', 'dae.collocation')
        # ncp = kwargs.pop('ncp', 3)
        # nfe = kwargs.pop('nfe', 50)
        # scheme = kwargs.pop('scheme', 'LAGRANGE-RADAU')
        init_from_sim = kwargs.pop('init_from_sim', False)
        
        if estimator == 'v_estimator':
            Estimator = VarianceEstimator
            est_str = 'VarianceEstimator'
            
        elif estimator == 'p_estimator':
            Estimator = ParameterEstimator
            est_str = 'ParameterEstimator'
            
        else:
            raise ValueError('Keyword argument estimator must be p_estimator or v_estimator.')  
        
        model_to_clone = self.model
        # if init_from_sim:
        #     self.simulate()
        #     model_to_clone = self.s_model
        #     print('Sim finished')
        
        setattr(self, f'{estimator[0]}_model', model_to_clone.clone())
        setattr(self, estimator, Estimator(getattr(self, f'{estimator[0]}_model')))
        getattr(self, estimator).apply_discretization(self.settings.collocation.method,
                                                      ncp=self.settings.collocation.ncp,
                                                      nfe=self.settings.collocation.nfe,
                                                      scheme=self.settings.collocation.scheme)
        
        self._from_trajectories(estimator)
        
        if init_from_sim and estimator == 'p_estimator':
            self.iniitalize_from_simulation(estimator=estimator)
        
        return None
    
    # def solve_variance_given_delta(self):
    #     """Wrapper for this VarianceEstimator function"""
    #     variances = self.v_estimator.solve_sigma_given_delta(**self.settings.variance_estimator)
    #     return variances
        
    def run_ve_opt(self, *args, **kwargs):
        """Wrapper for run_opt method in VarianceEstimator"""
        
        kwargs.update(self.settings.variance_estimator)
        
        if kwargs['method'] == 'direct_sigmas':
            worst_case_device_var = self.v_estimator.solve_max_device_variance(**kwargs)
            kwargs['device_range'] = (self.settings.variance_estimator.best_accuracy, worst_case_device_var)
            
        self._run_opt('v_estimator', *args, **kwargs)
        
        return None
    
    def run_pe_opt(self, *args, **kwargs):
        """Wrapper for run_opt method in ParameterEstimator"""
        
        self._run_opt('p_estimator', *args, **kwargs)
        return None
    
    def _update_related_settings(self):
        
        # Start with what is known
        if self.settings.parameter_estimator['covariance']:
            if self.settings.parameter_estimator['solver'] not in ['k_aug', 'ipopt_sens']:
                raise ValueError('Solver must be k_aug or ipopt_sens for covariance matrix')
        
        # If using sensitivity solvers switch covariance to True
        if self.settings.parameter_estimator['solver'] in ['k_aug', 'ipopt_sens']:
            self.settings.parameter_estimator['covariance'] = True
        
        #Subset of lambdas
        if self.settings.variance_estimator['freq_subset_lambdas'] is not None:
            if type(self.settings.variance_estimator['freq_subset_lambdas'], int):
                self.settings.variance_estimator['subset_lambdas' ] = self.reduce_spectra_data_set(self.settings.variance_estimator['freq_subset_lambdas']) 
        
        if self.settings.general.scale_pe and not self.settings.general.no_user_scaling:
            self.settings.solver.nlp_scaling_method = 'user-scaling'
    
        if self.settings.variance_estimator.max_device_variance:
            self.settings.parameter_estimator.model_variance = False
    
    def fix_parameter(self, param_to_fix):
        
        if not hasattr(self, 'fixed_params'):
            self.fixed_params = []
        
        if isinstance(param_to_fix, str):
            param_to_fix = [param_to_fix]
            
        self.fixed_params += [p for p in param_to_fix]
    
    def run_opt(self, init_from_sim=False):
        """Run ParameterEstimator but checking for variances - this should
        remove the VarianceEstimator being required to be implemented by the user
        
        """
        if self.model is None:    
            self.create_pyomo_model()  
        
        if not self.allow_optimization:
            raise ValueError('The model is incomplete for parameter optimization')
            
        # Some settings are required together, this method checks this
        self._update_related_settings()
        
        # Check if all component variances are given; if not run VarianceEstimator
        has_spectral_data = 'spectral' in [d.category for d in self.datasets]
        has_all_variances = self.components.has_all_variances
        variances_with_delta = None
        
        settings_dict = {**self.settings.collocation, **{'init_from_sim': init_from_sim}}
            
        if self.settings.variance_estimator.method == 'direct_sigmas':
            raise ValueError('This variance method is not intended for use in the manner: see Ex_13_direct_sigma_variances.py')
        
        if not has_all_variances and has_spectral_data:
            """If the data is spectral and not all variances are provided, VE needs to be run"""
            
            self.create_estimator(estimator='v_estimator', **settings_dict)
            settings_run_ve_opt = self.settings.variance_estimator
            
            if self.settings.variance_estimator.max_device_variance:
                max_device_variance = self.v_estimator.solve_max_device_variance(**settings_run_ve_opt)
            
            # elif self.settings.variance_estimator.use_delta:
            #     variances_with_delta = self.solve_variance_given_delta()

            else:
                self.run_ve_opt(**settings_run_ve_opt)
                
        elif not has_all_variances and not has_spectral_data:
            for comp in self.components:
                try:
                    comp.variance = self.variances[comp.name]
                except:
                    print(f'No variance information for {comp.name} found, setting equal to unity')
                    comp.variance = 1
                
        # Create ParameterEstimator
        self.create_estimator(estimator='p_estimator', **settings_dict)
        
        # if hasattr(self.p_model, 'Y'):    
        #     for k, v in self.p_model.Y.items():
        #         print(k)
        #         v.setlb(0)
        #         v.setub(1)
        
        #if self.settings.parameter_estimator.G_contribution is not None:
            #self._unwanted_G_initialization(self.p_model)
        variances = self.components.variances
        self.variances = variances
        
        # If variance calculated using VarianceEstimator, initialize PE isntance
        if 'v_estimator' in self.results_dict:
            if self.settings.general['initialize_pe']:
                self.initialize_from_variance_trajectory()
            if self.settings.general['scale_pe']:
                self.scale_variables_from_variance_trajectory()
            self.variances = self.results_dict['v_estimator'].sigma_sq
        
        elif self.settings.variance_estimator.max_device_variance:
            self.variances = max_device_variance
        
        # elif variances_with_delta is not None: 
        #     variances = variances_with_delta
            
        if self.settings.general['scale_variances']:
            self.variances = self._scale_variances(variances)
        
        settings_run_pe_opt = self.settings.parameter_estimator
        settings_run_pe_opt['solver_opts'] = self.settings.solver
        settings_run_pe_opt['variances'] = self.variances
        
        self.run_pe_opt(**settings_run_pe_opt)
        self.results = self.results_dict['p_estimator']
        self.results.file_dir = self.settings.general.charts_directory
        
        self.optimized = True
        
        return self.results
    
    @staticmethod
    def _scale_variances(variances):
        
        max_var = max(variances.values())
        scaled_vars = {comp: var/max_var for comp, var in variances.items()}
        return scaled_vars

    def _run_opt(self, estimator, *args, **kwargs):
        """Runs the respective optimization for the estimator"""
        
        if not hasattr(self, estimator):
            raise AttributeError(f'ReactionModel has no attribute {estimator}')
            
        self.results_dict[estimator] = getattr(self, estimator).run_opt(*args, **kwargs)
        return self.results_dict[estimator]
    
    def initialize_from_variance_trajectory(self, variable=None, obj='p_estimator'):
        """Wrapper for the initialize_from_trajectory method in
        ParameterEstimator
        
        """
        source = self.results_dict['v_estimator']
        self._from_trajectory('initialize', variable, source, obj)
        return None
    
    def initialize_from_trajectory(self, variable_name=None, source=None):
        """Wrapper for the initialize_from_trajectory method in
        ParameterEstimator or PyomoSimulator
        
        """
        self._var_to_initialize_from_trajectory.append([variable_name, source])
        return None
    
    def scale_variables_from_variance_trajectory(self, variable=None, obj='p_estimator'):
        """Wrapper for the scale_varialbes_from_trajectory method in
        ParameterEstimator
        
        """
        source = self.results_dict['v_estimator']
        self._from_trajectory('scale_variables', variable, source, obj)
        return None
        
    @staticmethod
    def _get_source_data(source, var):
        """Get the correct data from a ResultsObject or a DataFrame"""
        
        if isinstance(source, pd.DataFrame):
            return source
        else:
            return getattr(source, var)
    
    def _from_trajectory(self, category, variable, source, obj):
        """Generic initialization/scaling function"""
        
        estimator = getattr(self, obj)
        method = getattr(estimator, f'{category}_from_trajectory')
        
        if variable is None:
            for var in ['Z', 'C', 'S']:
            # for var in get_vars(self.)
                method(var, self._get_source_data(source, var))   
        else:
            method(variable, self._get_source_data(source, variable))
        return None
    
    def fix_from_trajectory(self, variable_name, variable_index, trajectories):
        """Wrapper for fix_from_trajectory in PyomoSimulator. This stores the
        information and then fixes the data after the simulator or estimator
        has been declared
        
        """
        self._var_to_fix_from_trajectory.append([variable_name, variable_index, trajectories])
        return None
                                               
    def set_known_absorbing_species(self, *args, **kwargs):
        """Wrapper for set_known_absorbing_species in TemplateBuilder
        
        """
        self.builder.set_known_absorbing_species(*args, **kwargs)    
        return None
    
    def scale(self):
        """Scale the model"""
        
        parameter_dict = self.parameters.as_dict(bounds=False)    
        scaled_parameter_dict, scaled_models_dict = scale_models(self.model,
                                                                 parameter_dict,
                                                                 name=self.name,
                                                                 )         
        return scaled_parameter_dict, scaled_models_dict
    
    def rhps_method(self,
                     method='k_aug',
                     calc_method='global',
                     scaled=True):
        """This calls the reduce_models method in the EstimationPotential
        module to reduce the model based on the reduced hessian parameter
        selection method.
        
        Args:
            kwargs:
                replace (bool): defaults to True, option to replace the
                    parameters deemed unestimable from the model with constants
                no_scaling (bool): defaults to True, removes the scaling
                    constants from the model and restores the parameter values
                    and their bounds.
                    
        Returns:
            results (ResultsObject): A standard results object with the reduced
                model results
        
        """
        if self.model is None:
            self.create_pyomo_model()
            
        kwargs = {}
        kwargs['solver_opts'] = self.settings.solver
        kwargs['method'] = method
        kwargs['calc_method'] = calc_method
        kwargs['scaled'] = scaled
        kwargs['use_bounds'] = False
        kwargs['use_duals'] = False
        
        parameter_dict = self.parameters.as_dict(bounds=True)
        results, reduced_model = rhps_method(self.model, **kwargs)
        
        results.file_dir = self.settings.general.charts_directory
        
        #self.reduced_model = reduced_model
        #self.using_reduced_model = True
        #self.reduced_model_results = results
        
        # Make a KipetModel as the result using the reduced model
        
        items_not_copied = {'model': False,
                            'parameters': False,
                            }
        
        reduced_kipet_model = self.clone('reduced_model', **items_not_copied)
        
        
        # reduced_kipet_model.add_parameter()
        # self, name=None, init=None, bounds=None
        
        reduced_kipet_model.model = reduced_model
        reduced_kipet_model.results = results
        
        reduced_parameter_set = {k: [v.value, (v.lb, v.ub)] for k, v in reduced_kipet_model.model.P.items()}
        for param, param_data in reduced_parameter_set.items():
            reduced_kipet_model.add_parameter(param, init=param_data[0], bounds=param_data[1])
        
        #print(reduced_parameter_set)
        
        # clone(self, *args, **kwargs):
        # """Makes a copy of the ReactionModel and removes the data. This is done
        # to reuse the model, components, and parameters in an easier manner
        
        # """
        # new_kipet_model = copy.deepcopy(self)
        
        # name = kwargs.get('name', self.name + '_copy')
        # copy_model = kwargs.get('model', True)
        # copy_builder = kwargs.get('builder', True)
        # copy_components = kwargs.get('components', True)   
        # copy_parameters = kwargs.get('parameters', True)
        # copy_datasets = kwargs.get('datasets', True)
        # copy_constants = kwargs.get('constants', True)
        # copy_settings = kwargs.get('settings', True)
        # copy_algebraic_variables = kwargs.get('alg_vars', True)
        # copy_odes = kwargs.get('odes', True)
        # copy_algs = kwargs.get('algs', True)
        
        
        return reduced_kipet_model
    
    # def reduce_model_old(self, **kwargs):
    #     """This calls the reduce_models method in the EstimationPotential
    #     module to reduce the model based on the reduced hessian parameter
    #     selection method.
        
    #     Args:
    #         kwargs:
    #             replace (bool): defaults to True, option to replace the
    #                 parameters deemed unestimable from the model with constants
    #             no_scaling (bool): defaults to True, removes the scaling
    #                 constants from the model and restores the parameter values
    #                 and their bounds.
                    
    #     Returns:
    #         results (ResultsObject): A standard results object with the reduced
    #             model results
        
    #     """
    #     if self.model is None:
    #         self.create_pyomo_model()
        
    #     parameter_dict = self.parameters.as_dict(bounds=True)
        
    #     kwargs['times'] = (self.model.start_time.value, self.model.end_time.value)
        
    #     print(kwargs)
        
    #     reduce_model_old(self, **kwargs)
        
    #     # self.reduced_model = reduced_model
    #     # self.using_reduced_model = True
    #     # self.reduced_model_results = results
        
    #     return None #results
    
    def set_non_absorbing_species(self, non_abs_list):
        """Wrapper for set_non_absorbing_species in TemplateBuilder"""
        
        self._has_non_absorbing_species = True
        self.non_abs_list = non_abs_list
        return None
        
    def add_noise_to_data(self, var, noise, overwrite=False):
        """Wrapper for adding noise to data after data has been added to
        the specific ReactionModel
        
        """
        dataframe = self.datasets[var].data
        if overwrite:
            self.datasets[var].data = dataframe
        return data_tools.add_noise_to_signal(dataframe, noise)    
    
#     def apply_pe_discretization(self, model_object, *args, **kwargs):
#         """Checks is the model is discretized and discretizes it in the case
#         that it is not
        
#         Args:
#             model (ConcreteModel): A pyomo ConcreteModel
            
#             ncp (int): number of collocation points used
            
#             nfe (int): number of finite elements used
            
#         Returns:
#             None
            
#         """
#         method = kwargs.pop('method', 'dae.collocation')
#         ncp = kwargs.pop('ncp', 3)
#         nfe = kwargs.pop('nfe', 50)
#         scheme = kwargs.pop('scheme', 'LAGRANGE-RADAU')
        
#         if not model_object.alltime.get_discretization_info():
        
#             # You need to change this out of an Estimator
#             model_pe = ParameterEstimator(model_object)
#             model_pe.apply_discretization(method,
#                                           ncp=ncp,
#                                           nfe=nfe,
#                                           scheme=scheme)
        
#         return None
        
#     def rule_objective(self, model):
#         """This function defines the objective function for the estimability
        
#         This is equation 5 from Chen and Biegler 2020. It has the following
#         form:
            
#         .. math::
#             \min J = \frac{1}{2}(\mathbf{w}_m - \mathbf{w})^T V_{\mathbf{w}}^{-1}(\mathbf{w}_m - \mathbf{w})
            
#         Originally KIPET was designed to only consider concentration data in
#         the estimability, but this version now includes complementary states
#         such as reactor and cooling temperatures. If complementary state data
#         is included in the model, it is detected and included in the objective
#         function.
        
#         Args:
#             model (pyomo.core.base.PyomoModel.ConcreteModel): This is the pyomo
#             model instance for the estimability problem.
                
#         Returns:
#             obj (pyomo.environ.Objective): This returns the objective function
#             for the estimability optimization.
        
#         """
#         obj = 0
        
#         from pyomo.environ import Objective
        
#         print(model.sigma)
    
#         for k in set(model.mixture_components.value_list) & set(model.measured_data.value_list):
#             for t, v in model.Cm.items():
#                 obj += 0.5*(model.Cm[t] - model.Z[t]) ** 2 /  1#model.sigma[k]**2
        
#         for k in set(model.complementary_states.value_list) & set(model.measured_data.value_list):
#             for t, v in model.U.items():
#                 obj += 0.5*(model.X[t] - model.U[t]) ** 2 / 1#model.sigma[k]**2      
    
#         model.objective = Objective(expr=obj)
    
#         return None


    def analyze_parameters(self, 
                        method=None,
                        parameter_uncertainties=None,
                        meas_uncertainty=None,
                        sigmas=None,
                        ):
        
        """This is a wrapper for the EstimabilityAnalyzer 
        """
        # Here we use the estimability analysis tools
        self.e_analyzer = EstimabilityAnalyzer(self.model)
        # Problem needs to be discretized first
        self.e_analyzer.apply_discretization('dae.collocation',
                                             nfe=60,
                                             ncp=1,
                                             scheme='LAGRANGE-RADAU')
        
        #param_uncertainties = {'k1':0.09,'k2':0.01,'k3':0.02,'k4':0.5}
        # sigmas, as before, represent the variances in regard to component
        #sigmas = {'A':1e-10,'B':1e-10,'C':1e-11, 'D':1e-11,'E':1e-11,'device':3e-9}
        # measurement scaling
        #meas_uncertainty = 0.05
        # The rank_params_yao function ranks parameters from most estimable to least estimable 
        # using the method of Yao (2003). Notice the required arguments. Returns a dictionary of rankings.
        if method == 'yao':
            
            listparams = self.e_analyzer.rank_params_yao(meas_scaling=meas_uncertainty,
                                                         param_scaling=parameter_uncertainties,
                                                         sigmas=sigmas)
            print(listparams)
            
            # Now we can run the analyzer using the list of ranked parameters
            params_to_select = self.e_analyzer.run_analyzer(method='Wu', 
                                                            parameter_rankings=listparams,
                                                            meas_scaling=meas_uncertainty, 
                                                            variances=sigmas
                                                            )
            # We can then use this information to fix certain parameters and run the parameter estimation
            print(params_to_select)
            
            params_to_fix = list(set(self.parameters.names).difference(params_to_select))
        
        return params_to_select, params_to_fix 
    
    def fix_and_remove_parameters(self, model_name, parameters=None):
        
        if model_name not in ['s_model', 'v_model', 'p_model']:
            raise ValueError(f'ReactionModel does not have model type {model_name}')
        
        model = getattr(self, model_name)
        param_replacer = ParameterReplacer([model], fix_parameters=parameters)
        param_replacer.remove_fixed_vars()
    
        return None
    
    
    @property
    def models(self):
        
        output = 'ReactionModel has the following:\n'
        output_dict = {}
        
        for model in [name + 'model' for name in ['', 's_', 'v_', 'p_']]:
        
            if hasattr(self, model):
                output += f'{model} True\n'
                output_dict[model] = True
            else:
                output += f'{model} False\n'
                output_dict[model] = False
            
        print(output)
        return output_dict
    
    @property
    def has_objective(self):
        """Check if p_model has an objective"""
        
        return hasattr(self.p_model, 'objective')
            
def _set_directory(model_object, filename, abs_dir=False):
    """Wrapper for the set_directory method. This replaces the awkward way
    of ensuring the correct directory for the data is used.
    
    Args:
        filename (str): the file name to be formatted
        
    Returns:
        file_path (pathlib Path): The absolute path of the given file
    """
    directory = model_object.settings.general.data_directory
    file_path = pathlib.Path(directory).joinpath(filename)
    
    return file_path