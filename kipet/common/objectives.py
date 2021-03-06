"""
KIPET 2020

This file contains the object functions used throughout Kipet modules in one
place.

"""
from pyomo.environ import (
    Objective,
    )

def get_objective(model, *args, **kwargs):
    
    objective_type = kwargs.get('objective_type', 'concentration')
    
    if objective_type == 'concentration':
        objective_expr = conc_objective(model, *args, **kwargs)

    return Objective(rule=objective_expr)


def conc_objective(model, *args, **kwargs):
    """
    
    Parameters
    ----------
    m : Pyomo ConcreteModel
        This is the current used in parameter fitting

    Returns
    -------
    obj : Objective function for Pyomo models
        This is the concentration based objective function

    """
    obj=0
  
    source = kwargs.get('source', 'concentration')
    
    if source == 'concentration':
    
        if model.mixture_components & model.measured_data:
            for index, values in model.Cm.items():
                obj += _concentration_term(model, index, var='Cm', **kwargs)
      
    elif source == 'spectra':
      
        if model.mixture_components:
            for index, values in model.C.items():
                obj += _concentration_term(model, index, var='C', **kwargs)
      
    return obj

def comp_objective(model, *args, **kwargs):
    """
    
    Parameters
    ----------
    m : Pyomo ConcreteModel
        This is the current used in parameter fitting

    Returns
    -------
    obj : Objective function for Pyomo models
        This is the concentration based objective function

    """
    obj=0
    
    if model.complementary_states & model.measured_data:
        for index, values in model.U.items():
            obj += _complementary_state_term(model, index)
        
    return obj

def spectra_objective(model, *args, **kwargs):
    """
    
    Parameters
    ----------
    m : Pyomo ConcreteModel
        This is the current used in parameter fitting

    Returns
    -------
    obj : Objective function for Pyomo models
        This is the concentration based objective function

    """
    obj=0
    # change this to items in the list (D or D_bar)
    # for t in model.meas_times:
    #     for l in model.meas_lambdas:
    
    for index, values in model.D.items():
        obj += _spectra_term(model, index)
        
    return obj

def absorption_objective(model, *args, **kwargs):
    """
    
    Parameters
    ----------
    m : Pyomo ConcreteModel
        This is the current used in parameter fitting

    Returns
    -------
    obj : Objective function for Pyomo models
        This is the concentration based objective function

    """
    sigma_device = kwargs.get('device_variance', 1)
    g_option = kwargs.get('g_option', None)
    with_d_vars = kwargs.get('with_d_vars', True)
    shared_spectra = kwargs.get('shared_spectra', True)
    list_components = kwargs.get('species_list', None)

    obj=0

    for index, values in model.D.items():
        obj += _spectral_term_MEE(model,
                                  index,
                                  sigma_device,
                                  g_option,
                                  shared_spectra,
                                  with_d_vars,
                                  list_components)
    return obj

# def calc_D_bar(model, D_bar_use, list_components):
    
#     if D_bar_use is False:
#         D_bar = model.D_bar
#     else:
#         D_bar = {}
#         if hasattr(model, '_abs_components'):
#             d_bar_list = model._abs_components
#             c_var = 'Cs'
#         else:
#             d_bar_list = list_components
#             c_var = 'C'    
            
#         if hasattr(model, 'huplc_absorbing') and hasattr(model, 'solid_spec_arg1'):
#             d_bar_list = [k for k in d_bar_list if k not in model.solid_spec_arg1]
                 
#         for t in model.meas_times:
#             for l in model.meas_lambdas:
#                 D_bar[t, l] = sum(getattr(model, c_var)[t, k] * model.S[l, k] for k in d_bar_list)

#     return D_bar
        
def _concentration_term(model, index, var='C', **kwargs):
    """
    
    Parameters
    ----------
    m : Pyomo ConcreteModel
        This is the current used in parameter fitting

    index : tuple
        This is the index of the model.C component

    Returns
    -------
    objective_concentration_term : Pyomo expression
        LS concentration term for objective

    """
    custom_sigma = kwargs.get('variance', None)
    
    if custom_sigma is None:
        variance = model.sigma[index[1]]
    else:
        variance = custom_sigma[index[1]]
        
    if variance is None:
        variance = 1
    
    objective_concentration_term = (getattr(model, var)[index] - model.Z[index]) ** 2  / variance
    
    return objective_concentration_term
    
def _complementary_state_term(model, index, **kwargs):
    """
    
    Parameters
    ----------
    m : Pyomo ConcreteModel
        This is the current used in parameter fitting

    index : tuple
        This is the index of the model.C component

    Returns
    -------
    objective_complementary_state_term : Pyomo expression
        LS complementary state term for objective

    """
    custom_sigma = kwargs.get('variance', None)
    
    if custom_sigma is None:
        variance = model.sigma[index[1]]
    else:
        variance = custom_sigma[index[1]]
    
    objective_complementary_state_term = (model.U[index] - model.X[index]) ** 2 / variance
    
    return objective_complementary_state_term

def _spectra_term(model, index, use_sigma=True):
    """
    
    Parameters
    ----------
    m : Pyomo ConcreteModel
        This is the current used in parameter fitting

    index : tuple
        This is the index of the model.C component

    Returns
    -------
    objective_complementary_state_term : Pyomo expression
        LS complementary state term for objective

    """
    objective_complementary_state_term = 0.5*(model.D[index] - model.D_bar[index]) ** 2
    
    if use_sigma:
        objective_complementary_state_term /= model.sigma['device']**2

    return objective_complementary_state_term
    
def _absorption_term(model, index, sigma_device=1, D_bar=None, g_options=None):
    
    print(index)
    print(model.D[index])
    print(D_bar[index])
    
    if g_options['unwanted_G'] or g_options['time_variant_G']:
        objective_absorption_term = (model.D[index] - D_bar[index] - model.qr[index[0]]*model.g[index[1]]) ** 2 / sigma_device
    elif g_options['time_invariant_G_no_decompose']:
        objective_absorption_term = (model.D[index] - D_bar[index] - model.g[index[1]]) ** 2 / sigma_device
    else:
        objective_absorption_term = (model.D[index] - D_bar[index]) ** 2 / sigma_device
        
    print(f'The index: {index}')
    print(objective_absorption_term.to_string())
    print('\n #### \n')

    return objective_absorption_term
    
def _spectral_term_MEE(model, index, sigma_device, g_option, shared_spectra, with_d_vars, list_components):
    
    t = index[0]
    l = index[1]
    if with_d_vars:
        base = model.D[t, l] - model.D_bar[t, l]
    else:
        D_bar = sum(model.C[t, k] * model.S[l, k] for k in list_components)
        base = model.D[t, l] - D_bar
        
    G_term = 0
    if g_option == 'time_variant_G':
        G_term -= model.qr[t]*model.g[l]
    elif g_option == 'time_invariant_G_decompose' and shared_spectra or g_option == 'time_invariant_G_no_decompose':
        G_term -= model.g[l]
   
    objective_spectral_term = (base + G_term)**2/sigma_device
    return objective_spectral_term

