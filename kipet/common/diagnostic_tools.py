"""
Diagnostic Tools used in Kipet
"""
import numpy as np
import pandas as pd
import scipy


def rank(A, eps=1e-10):
    """ obtains the rank of a matrix based on SVD
    
        Args:
            eps (optional, float): the value of the singular values that corresponds to 0 
                            when smaller than eps. Default = 1e-10

        Returns:
            rank (int): The rank of the matrix

    """
    print(type(A))  
    if isinstance(A, np.matrix):
        u, s, vh = np.linalg.svd(A)
        return len([x for x in s if abs(x) > eps]) 
    elif isinstance(A, pd.core.frame.DataFrame): 
        A = np.array(A)
        U, s, V = np.linalg.svd(A, full_matrices=True)
        return len([x for x in s if abs(x) > eps]) 
    else:
        raise RuntimeError("Must provide A as either numpy matrix or pandas dataframe")

def nullspace(A, atol=1e-13, rtol=0):
    """ obtains the nullspace of a matrix based on SVD. Taken from the SciPy cookbook
    
        Args:
            atol : float
        The absolute tolerance for a zero singular value.  Singular values
        smaller than `atol` are considered to be zero.
            rtol : float
        The relative tolerance.  Singular values less than rtol*smax are
        considered to be zero, where smax is the largest singular value.

        Returns:
           ns : ndarray
        If `A` is an array with shape (m, k), then `ns` will be an array
        with shape (k, n), where n is the estimated dimension of the
        nullspace of `A`.  The columns of `ns` are a basis for the
        nullspace; each element in numpy.dot(A, ns) will be approximately
        zero.

    """
    A = np.atleast_2d(A)
    u, s, vh = scipy.linalg.svd(A)
    tol = max(atol, rtol * s[0])
    nnz = (s >= tol).sum()
    ns = vh[nnz:].conj().T
    return ns
       
def basic_pca(dataFrame,n=None,with_plots=False):
    """ Runs basic component analysis based on SVD
    
        Args:
            dataFrame (DataFrame): spectral data
            
            n (int): number of largest singular-values
            to plot
            
            with_plots (boolean): argument for files with plots due to testing

        Returns:
            None

    """
            
    times = np.array(dataFrame.index)
    lambdas = np.array(dataFrame.columns)
    D = np.array(dataFrame)
    #print("D shape: ", D.shape)
    U, s, V = np.linalg.svd(D, full_matrices=True)
    #print("U shape: ", U.shape)
    #print("s shape: ", s.shape)
    #print("V shape: ", V.shape)
    #print("sigma/singular values", s)
    if n == None:
        print("WARNING: since no number of components is specified, all components are printed")
        print("It is advised to select the number of components for n")
        n_shape = s.shape
        n = n_shape[0]
        
    u_shape = U.shape
    #print("u_shape[0]",u_shape[0])
    n_l_vector = n if u_shape[0]>=n else u_shape[0]
    n_singular = n if len(s)>=n else len(s)
    idxs = range(n_singular)
    vals = [s[i] for i in idxs]
    v_shape = V.shape
    n_r_vector = n if v_shape[0]>=n else v_shape[0]
    
    if with_plots:
        for i in range(n_l_vector):
            plt.plot(times,U[:,i])
        plt.xlabel("time")
        plt.ylabel("Components U[:,i]")
        plt.show()
        
        plt.semilogy(idxs,vals,'o')
        plt.xlabel("i")
        plt.ylabel("singular values")
        plt.show()
        
        for i in range(n_r_vector):
            plt.plot(lambdas,V[i,:])
        plt.xlabel("wavelength")
        plt.ylabel("Components V[i,:]")
        plt.show()

def perform_data_analysis(dataFrame, pseudo_equiv_matrix, rank_data):  
    """ Runs the analysis by Chen, et al, 2018, based upon the pseudo-equivalency
    matrix. User provides the data and the pseudo-equivalency matrix and the analysis
    provides suggested number of absorbing components as well as whether there are
    likely to be unwanted spectral contributions.
    
        Args:
            dataFrame (DataFrame): spectral data
            
            pseudo_equiv_matrix (list of lists): list containing the rows of the pseudo-equivalency
                                matrix.
            
            rank_data (int): rank of the data matrix, as determined from SVD (number of coloured species)
                
            with_plots (boolean): argument for files with plots due to testing

        Returns:
            None

    """  
    if not isinstance(dataFrame, pd.DataFrame):
        raise TypeError("data must be inputted as a pandas DataFrame, try using read_spectral_data_from_txt or similar function first")
    
    if not isinstance(pseudo_equiv_matrix, list):
        raise TypeError("The Pseudo-equivalency matrix must be inputted as a list containing lists with each row of the pseudo-equivalency matrix")
    PEM = np.matrix(pseudo_equiv_matrix)
    rkp = rank(PEM)
    print("Rank of pseudo-equivalency matrix is ", rkp)
    
    ns = nullspace(PEM)
    print("Nullspace/kernel of pseudo-equivalency matrix is ", ns)
    if ns.size == 0:
        print("Null space of pseudo-equivalency matrix is null")
        rankns = 0
    else:
        rankns = rank(ns)
    
    print("the rank of the nullspace/kernel of pseudo-equivalency matrix is ", rankns)
    
    num_components = PEM.shape[1]
    if rankns > 0:
        ncr = num_components - rankns
        print("Choose the following number of absorbing species:", ncr)
    else:
        ncr = num_components
    ncms = rank_data
    
    if ncr == ncms:
        print("Solve standard problem assuming no unwanted contributions")
    elif ncr == ncms - 1:
        print("Solve with unwanted contributions")
    else:
        print("There may be uncounted for species in the model, or multiple sources of unknown contributions")
    