// Route management functions
const RouteManagement = {
    async getRoutes() {
        try {
            const response = await fetch('/api/routes');
            const data = await response.json();
            return data.routes;
        } catch (error) {
            console.error('Error fetching routes:', error);
            return [];
        }
    },

    async createRoute(routeData) {
        try {
            const response = await fetch('/api/routes', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(routeData)
            });
            return await response.json();
        } catch (error) {
            console.error('Error creating route:', error);
            throw error;
        }
    },

    async assignMachine(operationId, machineId) {
        try {
            const response = await fetch('/api/routes/assign-machine', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    operation_id: operationId,
                    machine_id: machineId
                })
            });
            return await response.json();
        } catch (error) {
            console.error('Error assigning machine:', error);
            throw error;
        }
    },

    async getMachineSuggestions(operationId) {
        try {
            const response = await fetch(`/api/suggest-machines/${operationId}`);
            const data = await response.json();
            if (data.status === 'success') {
                return data.suggestions;
            } else {
                throw new Error(data.message);
            }
        } catch (error) {
            console.error('Error getting machine suggestions:', error);
            throw error;
        }
    },

    async validateMachineAssignment(operationId, machineId) {
        try {
            const response = await fetch('/api/validate-machine', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    operation_id: operationId,
                    machine_id: machineId
                })
            });
            return await response.json();
        } catch (error) {
            console.error('Error validating machine assignment:', error);
            throw error;
        }
    }
}; 